# Services & Extensions

Services encapsulate long-lived behavior and shared integrations. Extensions provide Discord-facing commands, views, and event handlers that rely on those services.

## Service catalog

| Service | Module | Responsibilities |
| --- | --- | --- |
| Notifications | [`extensions/notifications/service.py`](../../extensions/notifications/service.py) | Consumes queue events to deliver Discord notifications and manages notification-related views. |
| Newsfeed | [`extensions/newsfeed/service.py`](../../extensions/newsfeed/service.py) | Generates the community newsfeed, composes embeds, and schedules refresh jobs. |
| XP | [`extensions/xp/service.py`](../../extensions/xp/service.py) | Tracks player progression, processes XP events from queues, and updates Discord roles or embeds. |
| Completions | [`extensions/completions/service.py`](../../extensions/completions/service.py) | Handles run submissions, uses the GenjiPK SDK, and triggers newsfeed updates. |
| Playtest | [`extensions/playtest/service.py`](../../extensions/playtest/service.py) | Coordinates playtest sessions and DM reminders. |
| Notifications Queue Worker | [`core/services/queue_worker.py`](../../core/services/queue_worker.py) | Boots RabbitMQ consumers, delegates jobs to registered handlers, and ensures graceful shutdown. |

> Extend this table as new services ship to keep a single authoritative map of long-lived processes.

## Extension anatomy

1. **Cog definition:** Each extension defines a cog that inherits from `BaseExtension` or `commands.Cog`.
2. **Dependency injection:** Cogs pull shared clients from the dependency container (`core.dependencies.container`).
3. **Queue registration:** Extensions that process background jobs decorate handler coroutines with `@register_queue_handler` to bind queue names.
4. **View/Embed helpers:** Presentation logic is centralized in `utilities/` helpers (formatters, view builders) to ensure consistent UX.

## Adding a new service

1. Create a module under `extensions/<feature>/service.py` (or `core/services/` if it is infrastructure-wide).
2. Subclass `BaseService` and implement `start`/`stop` hooks for setup and teardown.
3. Register the service with the dependency container in `core/dependencies/factory.py`.
4. Update `core/extensions.py` (or the relevant registry) so `setup_hook` starts the service.
5. Document the queue names, configuration keys, and embeds the service controls.

By following this structure, we maintain a consistent mental model of how features attach to the bot runtime.
