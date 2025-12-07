from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Callable, Type, TypeAlias, TypeVar, cast

import msgspec
from aio_pika.abc import AbstractIncomingMessage
from genjipk_sdk.internal import ClaimCreateRequest

if TYPE_CHECKING:
    import core

log = getLogger(__name__)

QueueHandler: TypeAlias = Callable[[AbstractIncomingMessage], Awaitable[None]]

TStruct = TypeVar("TStruct", bound=msgspec.Struct)
F = TypeVar("F", bound=Callable[..., Awaitable[None]])


def _get_bot(self: object) -> core.Genji:
    """Get the bot object from a service instance."""
    bot = getattr(self, "bot", None)
    if bot is None:
        raise RuntimeError("Service instance has no `.bot` attribute.")
    return cast("core.Genji", bot)


def queue_consumer(
    queue_name: str,
    *,
    struct_type: Type[TStruct],
    idempotent: bool = False,
    pytest_header: str = "x-pytest-enabled",
) -> Callable[[F], F]:
    """Decorator for defining RabbitMQ consumer handlers.

    This decorator standardizes how queue consumers decode messages,
    short-circuit pytest messages, and optionally enforce idempotency.
    It wraps a handler of the form::

        async def handler(self, event: StructType, message: AbstractIncomingMessage) -> None

    and returns a function compatible with the RabbitService consumer engine.

    The wrapper performs these steps:

    1. Checks for a pytest header and skips processing when present.
    2. Decodes the incoming message body into the specified ``struct_type`` using ``msgspec``.
    3. If ``idempotent`` is enabled:
       - Claims idempotency using ``bot.api.claim_idempotency``.
       - Skips processing when the message has already been consumed.
       - Automatically deletes the claim if the handler raises an exception.
    4. Calls the original handler with ``(self, event, message)``.

    Metadata is attached to the wrapper for later inspection by
    ``RabbitService`` (``_queue_name``, ``_struct_type``, ``_idempotent``).

    Args:
        queue_name (str):
            The name of the RabbitMQ queue this handler consumes from.
        struct_type (Type[TStruct]):
            The msgspec ``Struct`` type used to decode ``message.body``.
        idempotent (bool, optional):
            Whether message processing must be idempotent. If ``True``,
            the wrapper performs claim/cleanup logic using the bot's API.
            Defaults to ``False``.
        pytest_header (str, optional):
            The message header that, when truthy, causes the consumer
            to no-op (useful for integration tests). Defaults to ``"x-pytest-enabled"``.

    Returns:
        Callable[[F], F]:
            A decorator that transforms the handler into a wrapped version
            performing decoding, pytest filtering, and optional idempotency enforcement.

    Raises:
        RuntimeError:
            If the service instance passed as ``self`` does not expose a ``.bot`` attribute
            when idempotency is enabled.


    """

    def decorator(fn: F) -> F:
        async def wrapper(self: object, message: AbstractIncomingMessage) -> None:
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

        return cast(F, wrapper)

    return decorator


async def setup(_: core.Genji) -> None:
    """Dummy setup for extension loading."""
