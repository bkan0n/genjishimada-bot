# Genji Shimada Bot Documentation

Welcome to the reference hub for the Genji Shimada Discord bot. These pages are aimed at maintainers and contributors who need to understand how the runtime is structured, how background jobs are processed, and which conventions shape user-facing features.

## How to use this guide

- **Start with the architecture section** to learn how `main.py` boots the bot, how `core.Genji` loads extensions, and how shared services are attached.
- **Review the user experience guidelines** before crafting embeds or component-heavy views so that new features match the existing presentation.
- **Consult the operations section** when wiring environment variables, editing the TOML config files, or deploying containers.

## Repository quick facts

- **Runtime:** Python 3.13+ using `discord.py` (tracked from the upstream `master` branch) alongside `aiohttp`, `aio-pika`, and the `genjipk-sdk` client models.
- **Infrastructure:** The bot talks to the GenjiPK HTTP API, consumes RabbitMQ queues, and relies on Discord gateway intents configured in `core/genji.py`.
- **Local tooling:** [`just`](https://github.com/bkan0n/genjishimada-bot/blob/main/justfile) targets are provided for linting and running the bot, and the development dependencies (Ruff, BasedPyright, MkDocs) are managed via `uv`.

## Getting started

1. Clone the repository and install dependencies. With `uv` installed, run `uv sync --group dev` to create a virtual environment that includes MkDocs, Ruff, and the other dev tools specified in `pyproject.toml`.
2. Create a `.env` file (the entry point calls `load_dotenv(".env")`) and populate at least `DISCORD_TOKEN`, `BOT_ENVIRONMENT`, `API_KEY`, and RabbitMQ credentials (`RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `RABBITMQ_HOST`).
3. Adjust `configs/dev.toml` or `configs/prod.toml` so that the guild and channel identifiers match your Discord workspace. The `Genji` class loads `prod.toml` when `BOT_ENVIRONMENT` is `"production"` and `dev.toml` otherwise.
4. Launch the bot with `just run` or `uv run python main.py`. The startup sequence creates shared clients (API, RabbitMQ, services) through the individual extension `setup` functions described later in this guide.

> **Updating these docs?** See [Working on the Docs](contributing/docs-workflow.md) for details on running MkDocs locally and publishing changes.
