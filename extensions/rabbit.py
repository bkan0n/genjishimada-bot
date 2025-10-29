from __future__ import annotations

import asyncio
import os
import time
from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Callable, TypeAlias, TypeVar

from aio_pika import Channel, DeliveryMode, Message, connect_robust
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.pool import Pool

from extensions._queue_registry import (
    _registered_queue_handlers,
    finalize_queue_handlers,
)

if TYPE_CHECKING:
    from aio_pika.abc import AbstractRobustConnection

    import core

log = getLogger(__name__)

RABBITMQ_USER = os.getenv("RABBITMQ_DEFAULT_USER", "")
RABBITMQ_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "")

# DLQ processor config (no edits to existing constants)
DLQ_HEADER_KEY = os.getenv("DLQ_HEADER_KEY", "dlq_notified")
DLQ_PROCESS_INTERVAL = int(os.getenv("DLQ_PROCESS_INTERVAL", "60"))  # seconds between scans
DLQ_MAX_PER_QUEUE_TICK = int(os.getenv("DLQ_MAX_PER_QUEUE_TICK", "5000"))  # safety cap per scan


QueueHandler: TypeAlias = Callable[[AbstractIncomingMessage], Awaitable[None]]
F = TypeVar("F", bound=Callable[..., Awaitable[None]])


class RabbitClient:
    def __init__(self, bot: core.Genji) -> None:
        """Initialize a new RabbitClient instance.

        Args:
            bot (core.Genji): The Genji bot instance used for contextual operations.

        Initializes internal connection and channel pools for RabbitMQ operations, sets up
        startup synchronization primitives, and schedules the initial queue setup task.
        """
        self._bot = bot
        self._connection_pool = Pool(self._get_connection, max_size=2)
        self._channel_pool = Pool(self._get_channel, max_size=10)
        self._startup_drain_complete = asyncio.Event()
        self._startup_draining = True
        self._pending_startup_messages = 0

        self._queues: dict[str, QueueHandler] = {}
        self._dlq_suffix = ".dlq"

        self._setup_task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the RabbitMQ client by launching queue setup in the background."""
        self._queues = finalize_queue_handlers(self._bot)
        log.debug("[Rabbit] Queues to consume (resolved): %s", list(self._queues.keys()))
        self._setup_task = asyncio.create_task(self._set_up_queues())
        log.debug("[DLQ] Will scan: %s", self.list_target_dlqs())
        self.start_dlq_processor()

    def _bind_registered_handlers(self) -> None:
        for queue_name, fn in _registered_queue_handlers:
            log.debug(f"[⏳] Attempting to bind handler for '{queue_name}': {fn.__qualname__}")
            if hasattr(fn, "__self__"):  # Already bound method
                self._queues[queue_name] = fn
                log.debug(f"[✓] Bound bound method for queue '{queue_name}': {fn}")
            else:
                instance_name = fn.__qualname__.split(".")[0]
                instance = getattr(self._bot, instance_name, None)
                if instance:
                    bound_fn = getattr(instance, fn.__name__, None)
                    if bound_fn:
                        self._queues[queue_name] = bound_fn
                        log.debug(f"[✓] Bound method '{fn.__name__}' on {instance_name} to queue '{queue_name}'")
                        continue
                if fn.__name__ in globals():
                    self._queues[queue_name] = fn
                    log.debug(f"[✓] Bound global function '{fn.__name__}' to queue '{queue_name}'")
                else:
                    log.debug(f"[✗] Failed to bind queue handler '{fn.__name__}' for queue '{queue_name}'")

    async def _set_up_queues(self) -> None:
        """Declare and consume all registered queues, including DLQs.

        This method:
        - Waits for bot readiness.
        - Declares durable queues and associated dead-letter queues (DLQs).
        - Counts unacknowledged startup messages.
        - Begins consuming from each queue using wrapped handlers.
        - Signals completion once all startup messages are drained.
        """
        if not self._queues:
            log.warning("[✗] No queue handlers registered at startup.")

        await self._bot.wait_until_ready()
        log.debug(f"[→] Queues to consume: {list(self._queues.keys())}")
        for queue_name, handler in self._queues.items():
            log.debug(f"[x] Declaring queue: {queue_name}")
            channel = await self._get_channel()
            await channel.set_qos(prefetch_count=1)

            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": queue_name + self._dlq_suffix,
                },
            )

            await channel.declare_queue(queue_name + self._dlq_suffix, durable=True)

            declared = await channel.declare_queue(queue_name, passive=True)
            self._pending_startup_messages += declared.declaration_result.message_count or 0
            log.debug(
                f"[x] [RabbitMQ] Queue {queue_name} has "
                f"{declared.declaration_result.message_count} messages on startup."
            )

            log.debug(f"[⏳] Declaring and consuming queue: {queue_name}")
            await queue.consume(await self._wrap_handler(handler, queue_name))

        if self._pending_startup_messages == 0:
            self._startup_draining = False
            self._startup_drain_complete.set()
            log.debug("[✓] No startup messages to process. Marking as drained.")

    async def _get_connection(self) -> AbstractRobustConnection:
        try:
            return await connect_robust(f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}/")
        except Exception as e:
            log.error(f"[!] [RabbitMQ] Error connecting get_connection: {e}")
            raise

    async def _get_channel(self) -> Channel:
        try:
            async with self._connection_pool.acquire() as connection:
                return await connection.channel()
        except Exception as e:
            log.error(f"[!] [RabbitMQ] Error getting channel get_channel: {e}")
            raise

    async def publish(self, queue_name: str, json_data: bytes) -> None:
        """Publish a message to the specified RabbitMQ queue.

        Args:
            queue_name (str): The name of the queue to publish to.
            json_data (bytes): The serialized message payload in JSON format.

        Raises:
            aio_pika.exceptions.AMQPException: If publishing fails due to a connection or channel issue.
        """
        async with self._channel_pool.acquire() as channel:
            message = Message(json_data, delivery_mode=DeliveryMode.PERSISTENT)
            await channel.default_exchange.publish(message, routing_key=queue_name)
            log.debug(f"[→] Published message to {queue_name}")

    async def _wrap_handler(self, handler: QueueHandler, queue_name: str) -> QueueHandler:
        """Wrap a queue handler to manage errors and startup drain accounting.

        Args:
            handler (QueueHandler): The original message handler function.
            queue_name (str): The name of the queue being consumed.

        Returns:
            QueueHandler: A wrapped handler with error rejection and drain tracking.
        """
        log.debug(f"[→] Received message from queue '{queue_name}'")

        async def wrapped(message: AbstractIncomingMessage) -> None:
            try:
                async with message.process():
                    await handler(message)

                    if self._startup_draining:
                        self._pending_startup_messages -= 1
                        if self._pending_startup_messages <= 0:
                            log.debug("[✓] Startup drain complete.")
                            self._startup_draining = False
                            self._startup_drain_complete.set()

            except Exception:
                log.exception(f"[!] Error processing message from {queue_name}, publishing to DLQ.")

        return wrapped

    async def wait_until_drained(self) -> None:
        """Block until all startup messages have been processed and draining is complete.

        This method is typically used to defer downstream operations until all
        early messages are handled and the client is ready for steady-state operation.
        """
        try:
            await asyncio.wait_for(self._startup_drain_complete.wait(), timeout=None)
        except asyncio.TimeoutError:
            log.debug("[!] Startup drain timed out. Continuing anyway.")

    def start_dlq_processor(self) -> None:
        """Start a background task that periodically processes all DLQs.

        Safe to call after .start(). It snapshots each DLQ's depth and processes
        up to that many messages, republishing with a header to avoid loops.
        """
        if getattr(self, "_dlq_task", None) is None or self._dlq_task.done():
            self._dlq_task = asyncio.create_task(self._dlq_processor_loop())
            log.debug("[→] DLQ processor started.")

    async def _dlq_processor_loop(self) -> None:
        """Periodic DLQ sweep loop."""
        while True:
            try:
                processed = await self._process_all_dlqs_once()
                if processed:
                    log.info(f"[DLQ] processed {processed} message(s) across all DLQs")
            except Exception:
                log.exception("[DLQ] Unhandled error during DLQ processing loop")
            await asyncio.sleep(DLQ_PROCESS_INTERVAL)

    async def _process_all_dlqs_once(self) -> int:
        """Process each registered queue's DLQ once with a fixed cap per queue.

        Returns:
            int: Total number of DLQ messages processed in this sweep.
        """
        total = 0
        # Use a single channel for the sweep; it's fine for moderate volumes.
        async with self._channel_pool.acquire() as channel:
            await channel.set_qos(prefetch_count=100)
            for base_queue in self._queues:
                try:
                    n = await self._process_one_dlq(channel, base_queue)
                    total += n
                except Exception:
                    log.exception(f"[DLQ] Error processing DLQ for base queue '{base_queue}'")
        return total

    async def _process_one_dlq(self, channel: Channel, base_queue: str) -> int:
        """Republish at most <snapshot depth> messages from <base_queue>.dlq with a header.

        The 'snapshot depth' is taken at the start (message_count). We process up to that number,
        so the scan won't loop forever even as republished messages return to the DLQ.

        Returns:
            int: Number processed for this DLQ.
        """
        dlq_name = f"{base_queue}{self._dlq_suffix}"

        # Passive declare to get initial count without creating it.
        dlq = await channel.declare_queue(dlq_name, passive=True)
        # Snapshot the depth at the start; cap by DLQ_MAX_PER_QUEUE_TICK to be extra safe.
        initial_count = dlq.declaration_result.message_count or 0
        cap = min(initial_count, DLQ_MAX_PER_QUEUE_TICK)
        if cap == 0:
            return 0

        processed = 0
        # We re-declare a queue object to use .get() (dlq above is fine too; either works).
        queue = await channel.declare_queue(dlq_name, passive=True)

        while processed < cap:
            # Try non-blocking get with a tiny timeout to avoid hanging.
            try:
                msg = await queue.get(timeout=0.1, no_ack=False)
            except asyncio.TimeoutError:
                break
            if msg is None:
                break

            headers = dict(msg.headers or {})
            # If we've already marked it, just ack and move on.
            if headers.get(DLQ_HEADER_KEY) is True:
                await msg.nack(requeue=True)
                processed += 1
                continue

            guild = self._bot.get_guild(self._bot.config.guild)
            if not guild:
                raise RuntimeError("Why is there no guild")
            content = f"### {dlq_name}\n```json\n{msg.body}```"
            await guild.get_channel(1432862783644368968).send(content)

            # Republish a *copy* with the header set, then ack the original.
            new_headers = {**headers, DLQ_HEADER_KEY: True, "dlq_notified_at": int(time.time())}
            repub = Message(
                body=msg.body,
                headers=new_headers,
                content_type=msg.content_type,
                content_encoding=msg.content_encoding,
                delivery_mode=msg.delivery_mode,  # preserve persistence
                correlation_id=msg.correlation_id,
                message_id=msg.message_id,
                timestamp=msg.timestamp,
                type=msg.type,
                app_id=msg.app_id,
                user_id=msg.user_id,
            )

            # Publish right back to the DLQ by name via the default exchange.
            await channel.default_exchange.publish(repub, routing_key=dlq_name)
            await msg.ack()
            processed += 1

        if processed:
            log.debug(f"[DLQ] {dlq_name}: processed {processed}/{cap} (snapshot={initial_count})")
        return processed

    def list_target_dlqs(self) -> list[str]:
        """Return the DLQ names the processor will scan."""
        return [f"{q}{self._dlq_suffix}" for q in self._queues]


async def setup(bot: core.Genji) -> None:
    """Setup the message queue extension."""
    bot.rabbit = RabbitClient(bot)
