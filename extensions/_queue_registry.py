from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Callable, TypeAlias, TypeVar

from aio_pika.abc import AbstractIncomingMessage

QueueHandler: TypeAlias = Callable[[AbstractIncomingMessage], Awaitable[None]]
F = TypeVar("F", bound=Callable[..., Awaitable[None]])

_registered_queue_handlers: list[tuple[str, QueueHandler]] = []

if TYPE_CHECKING:
    import core


log = getLogger(__name__)


def register_queue_handler(queue_name: str) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        setattr(fn, "_queue_name", queue_name)
        _registered_queue_handlers.append((queue_name, fn))
        return fn

    return decorator


def get_registered_handlers() -> list[tuple[str, QueueHandler]]:
    return _registered_queue_handlers


def finalize_queue_handlers(bot: core.Genji) -> dict[str, QueueHandler]:
    queues: dict[str, QueueHandler] = {}

    log.debug(
        "Registered queue handlers: %s",
        [(q, getattr(fn, "__qualname__", str(fn))) for q, fn in _registered_queue_handlers],
    )

    for queue_name, fn in _registered_queue_handlers:
        if hasattr(fn, "__self__"):
            owner = getattr(fn, "__self__", None)
            if owner and hasattr(owner, "_wrap_job_status"):
                queues[queue_name] = owner._wrap_job_status(fn)  # type: ignore[attr-defined]  # noqa: SLF001
            else:
                queues[queue_name] = fn
            log.debug("Bound pre-bound handler for %s -> %s", queue_name, fn)
            continue

        # Search bot attributes for an instance that owns this method
        for attr_name in dir(bot):
            instance = getattr(bot, attr_name, None)
            if instance is None:
                continue

            bound_candidate = getattr(instance, fn.__name__, None)
            if not callable(bound_candidate):
                continue

            # IMPORTANT: attributes like _queue_name are on the underlying function
            func = getattr(bound_candidate, "__func__", bound_candidate)
            if getattr(func, "_queue_name", None) != queue_name:
                continue

            # If the instance inherits BaseService, use its wrapper to report job status
            if hasattr(instance, "_wrap_job_status"):
                queues[queue_name] = instance._wrap_job_status(bound_candidate)  # type: ignore[attr-defined]  # noqa: SLF001
                log.debug("Resolved & wrapped handler %s -> %s.%s", queue_name, type(instance).__name__, fn.__name__)
            else:
                queues[queue_name] = bound_candidate  # pyright: ignore[reportArgumentType]
                log.debug("Resolved handler %s -> %s.%s", queue_name, type(instance).__name__, fn.__name__)
            break
        else:
            log.warning("[✗] Could not resolve handler for queue %s", queue_name)

    log.debug("Queues resolved: %s", list(queues.keys()))
    return queues


async def setup(bot: core.Genji) -> None:
    """Dummy setup."""
