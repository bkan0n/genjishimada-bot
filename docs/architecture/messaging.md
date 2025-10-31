# Messaging & Queues

The bot relies on RabbitMQ for asynchronous work. Queue handlers are responsible for consuming messages, validating payloads, and triggering downstream updates.

## RabbitMQ integration

- The connection and channel lifecycle lives in [`core/messaging/rabbit.py`](https://github.com/bkan0n/genjishimada-bot/blob/main/core/messaging/rabbit.py).
- `core.messaging.registry` exposes `register_queue_handler` and `get_registered_handlers` to keep a central list of queues.
- The queue worker service (see [Services & Extensions](services.md)) consumes `QueueMessage` objects and dispatches them to handlers.

## Queue handler lifecycle

1. Decorate a coroutine with `@register_queue_handler("queue-name")`.
2. Type your payload with `msgspec` models or dataclasses.
3. The worker pulls the job, deserializes the payload, and invokes your coroutine with contextual information (logging, tracing, Sentry breadcrumbs).

### Error handling

- Handlers should raise domain-specific exceptions so the worker can determine retry vs. dead-lettering.
- Use `core.messaging.errors` for shared error types.
- The worker logs failures and reports them to Sentry if configured.

## Queue catalog

Document active queues here to keep the system transparent:

| Queue | Producer | Handler | Notes |
| --- | --- | --- | --- |
| `newsfeed.refresh` | Newsfeed service scheduler | `extensions.newsfeed.handlers.refresh_newsfeed` | Regenerates the community newsfeed embeds. |
| `notifications.dispatch` | Domain services emitting notifications | `extensions.notifications.handlers.dispatch_notification` | Sends DM or channel notifications with standardized embeds. |
| `xp.events` | Game telemetry ingestors | `extensions.xp.handlers.process_xp_event` | Applies XP gains/losses and updates leaderboard visuals. |

Add rows for new queues as they come online so maintainers can trace message flow quickly.
