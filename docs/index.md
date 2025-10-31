# Genji Shimada Bot Documentation

Welcome to the architecture and operations handbook for the Genji Shimada Discord bot. This site is designed for both internal maintainers and community contributors who want to understand how the bot is structured, how its background services communicate, and the patterns we follow when building new features.

## How to use this guide

- **Start with the architecture section** to learn how the bot boots, how extensions are registered, and how jobs flow through RabbitMQ.
- **Review the user experience guidelines** before crafting embeds, newsfeed entries, or interactive views so you can match the existing presentation layer.
- **Consult the operations section** when deploying new environments, rotating secrets, or debugging infrastructure.

## Repository quick facts

- **Runtime:** Python 3.13+ with `discord.py` and a collection of domain-specific extensions.
- **Infrastructure:** RabbitMQ for job queues, PostgreSQL for persistence, and optional Sentry monitoring.
- **Local tooling:** [`just`](https://github.com/bkan0n/genjishimada-bot/blob/main/justfile) recipes, Ruff linting, BasedPyright type checking, and container-based development via Docker Compose.

## Getting started

1. Clone the repository and install dependencies using `uv` or your preferred PEP 517 workflow.
2. Copy the example environment files in `configs/` and populate secrets.
3. Launch the local stack with `just run` or `docker compose -f docker-compose.dev.yml up`.
4. Use this documentation to understand the modules you are extending or debugging.

> **Need to update this site?** See [Working on the Docs](contributing/docs-workflow.md) for instructions on editing and publishing changes.
