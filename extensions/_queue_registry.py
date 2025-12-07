from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Callable, Type, TypeAlias, TypeVar

import msgspec
from aio_pika.abc import AbstractIncomingMessage
from genjipk_sdk.internal import ClaimCreateRequest

if TYPE_CHECKING:
    import core
    from utilities.base import BaseService

log = getLogger(__name__)

QueueHandler: TypeAlias = Callable[[AbstractIncomingMessage], Awaitable[None]]

TStruct = TypeVar("TStruct", bound=msgspec.Struct)
TService = TypeVar("TService", bound="BaseService")


def _get_bot(self: BaseService) -> core.Genji:
    """Get the bot object from a service instance.

    Statically, TService is bound to BaseService, which has `.bot: core.Genji`.
    """
    bot = getattr(self, "bot", None)
    if bot is None:
        raise RuntimeError("Service instance has no `.bot` attribute.")
    return bot


def queue_consumer(
    queue_name: str,
    *,
    struct_type: Type[TStruct],
    idempotent: bool = False,
    pytest_header: str = "x-pytest-enabled",
) -> Callable[
    [Callable[[TService, TStruct, AbstractIncomingMessage], Awaitable[None]]],
    Callable[[TService, AbstractIncomingMessage], Awaitable[None]],
]:
    """Decorator for RabbitMQ consumers.

    Usage:

        @queue_consumer("queue.name", struct_type=SomeEvent, idempotent=True)
        async def handler(self, event: SomeEvent, message: AbstractIncomingMessage) -> None:
            ...

    Behavior:
    - Attaches `_queue_name` metadata (for RabbitService discovery).
    - Always:
        * checks `pytest_header` in message.headers and short-circuits if truthy,
        * decodes `message.body` as `struct_type` via msgspec.
    - If `idempotent=True`:
        * claims idempotency using `bot.api.claim_idempotency(ClaimCreateRequest(message_id))`,
        * on handler exception, calls `bot.api.delete_claimed_idempotency(...)` and re-raises.
    """

    def decorator(
        fn: Callable[[TService, TStruct, AbstractIncomingMessage], Awaitable[None]],
    ) -> Callable[[TService, AbstractIncomingMessage], Awaitable[None]]:
        async def wrapper(self: TService, message: AbstractIncomingMessage) -> None:
            headers = message.headers or {}

            if headers.get(pytest_header, False):
                log.debug(
                    "[RabbitMQ] Pytest message received; skipping processing for %s.",
                    queue_name,
                )
                return

            event = msgspec.json.decode(message.body, type=struct_type)

            if not idempotent:
                await fn(self, event, message)
                return

            bot = _get_bot(self)
            api = bot.api

            claim_data: ClaimCreateRequest | None = None

            if message.message_id:
                claim_data = ClaimCreateRequest(message.message_id)
                res = await api.claim_idempotency(claim_data)
                if not res.claimed:
                    log.debug(
                        "[Idempotency] Duplicate message ignored: %s (%s)",
                        message.message_id,
                        queue_name,
                    )
                    return

            try:
                await fn(self, event, message)
            except Exception:
                if claim_data is not None:
                    try:
                        await api.delete_claimed_idempotency(claim_data)
                    except Exception:
                        log.exception(
                            "[Idempotency] Failed to delete claimed idempotency during error cleanup.",
                        )
                raise

        setattr(wrapper, "_queue_name", queue_name)
        setattr(wrapper, "_struct_type", struct_type)
        setattr(wrapper, "_idempotent", idempotent)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__

        return wrapper

    return decorator


async def setup(_: core.Genji) -> None:
    """Dummy setup for extension loading."""
