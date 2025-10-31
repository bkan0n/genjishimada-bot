# Core Bot Lifecycle

This page explains how the main Discord bot process boots, configures intents, and loads feature modules.

## Entry point

The bot starts from [`main.py`](../../main.py), which defines the `run_bot` coroutine. Key steps:

1. Load configuration via `load_settings` and environment variables.
2. Establish shared clients (PostgreSQL, RabbitMQ, HTTP) through the dependency container in `core.dependencies`.
3. Instantiate the `Genji` bot subclass with intents defined in `core.client`.
4. Register background services and extensions during `setup_hook`.

The `Genji` class in [`core/client.py`](../../core/client.py) extends `discord.ext.commands.Bot`. It configures:

- A custom `tree` sync strategy to keep slash commands consistent across guilds.
- Gateway intents aligned with our feature set (message content, reactions, scheduled events).
- Hooks to bind services (e.g., newsfeed, completions, XP) before the bot connects.

## Extension loading

Extensions live in the `extensions/` package. During startup, `Genji.setup_hook` iterates through the registry defined in [`core/extensions.py`](../../core/extensions.py) and loads each extension module. Each extension exposes a `setup` function that adds a cog or registers tasks.

### Base classes

- `BaseExtension` in [`core/extensions/base.py`](../../core/extensions/base.py) standardizes access to shared resources like the dependency container.
- `BaseService` in [`core/services/base.py`](../../core/services/base.py) provides lifecycle hooks (`start`, `stop`) and centralized error reporting.

## Startup sequencing

1. `setup_hook` awaits `BaseService.start` for registered services (notifications, completions, XP, etc.).
2. Queue handlers are registered through decorators (see [Messaging & Queues](messaging.md)).
3. Extension-specific scheduled tasks are scheduled after all services have initialized.

Use this section as a roadmap when adding new services—ensure they inherit from the appropriate base class, register with the dependency container, and participate in the startup lifecycle.
