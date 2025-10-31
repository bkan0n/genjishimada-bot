# Services & Extensions

Services encapsulate long-lived Discord behaviour, while extensions expose the asynchronous `setup` hook that registers those services and any associated cogs.

## Service catalog

| Service | Module / Class | Responsibilities |
| --- | --- | --- |
| API client | `extensions/api_client.py` → `APIClient` | Maintains an authenticated `aiohttp` session to the GenjiPK API, performs JSON encoding/decoding with `msgspec`, and exposes helpers for maps, completions, playtests, and notifications. The instance is assigned to `bot.api` during setup so other extensions can reuse it.【F:extensions/api_client.py†L20-L370】【F:extensions/api_client.py†L1549-L1555】
| RabbitMQ | `extensions/rabbit.py` → `RabbitClient` | Connects to RabbitMQ using pooled channels, declares queues (and their DLQs), wraps handlers registered through `register_queue_handler`, and exposes helper utilities such as `publish` and `wait_until_drained`. Assigned to `bot.rabbit` in the extension setup.【F:extensions/rabbit.py†L1-L227】【F:extensions/rabbit.py†L320-L323】
| Notifications | `extensions/notifications.py` → `NotificationService` | Determines whether a user has opted into specific notification bitmasks and sends DMs or channel pings accordingly. Stored on `bot.notifications` for use by other features.【F:extensions/notifications.py†L1-L73】【F:extensions/notifications.py†L88-L96】
| Newsfeed | `extensions/newsfeed.py` → `NewsfeedService` | Registers builders for each newsfeed payload type, publishes events into the configured channel, and consumes `api.newsfeed.create` queue messages to mirror content from the API. Attached to `bot.newsfeed` by the extension setup.【F:extensions/newsfeed.py†L606-L766】【F:extensions/newsfeed.py†L48-L59】【F:extensions/newsfeed.py†L728-L776】
| Completions | `extensions/completions.py` → `CompletionsManager` | Resolves submission, verification, and upvote channels; renders verification views; emits follow-up newsfeed events; and handles the completion-related RabbitMQ queues. The setup function assigns it to `bot.completions`.【F:extensions/completions.py†L485-L566】【F:extensions/completions.py†L585-L668】【F:extensions/completions.py†L723-L856】【F:extensions/completions.py†L1257-L1270】
| Playtest | `extensions/playtest.py` → `PlaytestManager` | Manages playtest threads, queue-driven state changes, and XP grants tied to votes. Registered on `bot.playtest` in the extension setup.【F:extensions/playtest.py†L247-L318】【F:extensions/playtest.py†L471-L618】【F:extensions/playtest.py†L1294-L1305】
| XP | `extensions/xp.py` → `XPManager` | Resolves XP channels, applies XP grants from the `api.xp.grant` queue, and exposes helpers for other services to award XP by type. Stored on `bot.xp` during setup.【F:extensions/xp.py†L126-L217】【F:extensions/xp.py†L187-L215】【F:extensions/xp.py†L218-L246】

> Extend this table as new extensions ship so there is a single map of the long-lived services attached to the bot instance.

## Extension anatomy

1. **Setup hook:** Each extension defines `async def setup(bot)` and attaches services, cogs, or background tasks.
2. **Shared state:** Services either use property setters on `Genji` (for example `bot.api`) or inherit from `utilities.base.BaseService` to gain guild/channel resolution helpers.【F:core/genji.py†L59-L110】【F:utilities/base.py†L158-L217】
3. **Queue registration:** Background work is tied to RabbitMQ queues by decorating handler coroutines with `@register_queue_handler("queue-name")`. The decorator stores metadata so `RabbitClient` can bind the functions when it starts consuming.【F:extensions/_queue_registry.py†L1-L47】【F:extensions/rabbit.py†L38-L120】
4. **Cross-extension collaboration:** Services call into one another via the properties on `bot`. For example, the completions flow calls `bot.api` to fetch payloads, uses `bot.notifications` to determine DM preferences, and leverages `bot.xp` to grant XP during verification updates.【F:extensions/completions.py†L632-L668】【F:extensions/completions.py†L705-L856】

Document queues, commands, and presentation helpers in the relevant sections below when adding new functionality so future contributors can follow the established patterns.
