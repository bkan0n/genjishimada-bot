# Core Bot Lifecycle

This page details how the main Discord process boots, wires shared services, and loads extensions.

## Entry point

The bot starts in [`main.py`](https://github.com/bkan0n/genjishimada-bot/blob/main/main.py) inside the `main()` coroutine. The sequence is:

1. Load environment variables from `.env`, configure Sentry (when `SENTRY_DSN` is present), and set up filtered Discord logging via `setup_logging()`.
2. Create an `aiohttp.ClientSession` and instantiate `core.Genji`, passing the configured prefix (`"?"` in production, `"!"` otherwise).
3. Start the asynchronous context manager for the bot and call `bot.start(DISCORD_TOKEN)` to connect to the Discord gateway.【F:main.py†L14-L83】

`core/genji.py` defines the `Genji` subclass of `commands.Bot`. During `__init__` the class:

- Applies the gateway intents defined at module level.
- Stores the shared HTTP session and constructs a `VideoThumbnailService` helper.
- Loads `configs/prod.toml` when `BOT_ENVIRONMENT` is `"production"`, or `configs/dev.toml` for all other environments, using the `utilities.config.decode` helper.【F:core/genji.py†L15-L57】【F:utilities/config.py†L1-L65】

## Extension loading

`Genji.setup_hook` runs once the Discord connection is preparing. It loads every module under `extensions/` (discovered by `extensions.__init__.EXTENSIONS`) plus the debugging cog `jishaku`. After extensions are loaded, the method schedules `self.rabbit.start()` on the bot loop so that queue consumers begin once all handlers are registered.【F:core/genji.py†L21-L50】【F:extensions/__init__.py†L1-L10】【F:extensions/rabbit.py†L320-L323】

Each extension exposes an async `setup(bot)` function that attaches services or cogs to the bot. Notable patterns include:

- `extensions.api_client.setup` instantiates `APIClient` and assigns it to `bot.api` for use across other modules.【F:extensions/api_client.py†L1549-L1555】
- `extensions.newsfeed.setup`, `extensions.completions.setup`, `extensions.playtest.setup`, and `extensions.xp.setup` create service classes that are stored on the bot for later access.【F:extensions/newsfeed.py†L48-L59】【F:extensions/completions.py†L1257-L1270】【F:extensions/playtest.py†L1294-L1305】【F:extensions/xp.py†L218-L246】
- `extensions.rabbit.setup` prepares the `RabbitClient`, which `setup_hook` starts after all handlers are registered.【F:extensions/rabbit.py†L1-L120】【F:extensions/rabbit.py†L320-L323】

## Service lifecycle

Service classes that inherit from `utilities.base.BaseService` (for example `CompletionsManager`, `PlaytestManager`, and `XPManager`) lazily resolve the configured guild and their target channels. The base class spawns a task that waits for the bot to become ready, fetches `bot.config.guild`, and calls each service's `_resolve_channels()` hook before handling work.【F:utilities/base.py†L158-L217】【F:extensions/completions.py†L485-L566】【F:extensions/playtest.py†L247-L318】【F:extensions/xp.py†L126-L217】

When adding a new feature module:

1. Create an extension module under `extensions/` with an `async def setup(bot)` entry point.
2. Attach any long-lived service to the bot (optionally inheriting from `BaseService`).
3. Register queue handlers with `register_queue_handler` if the feature processes RabbitMQ events (see [Messaging & Queues](messaging.md)).
4. Ensure the module is importable so that `extensions.__init__` discovers it automatically during startup.
