# Configuration & Deployment

Use this page to wire environment variables, TOML configuration, and deployment workflows for the Genji Shimada bot.

## Environment configuration

Runtime settings come from a combination of environment variables and a TOML file loaded at startup.

- **Core variables:**
  - `DISCORD_TOKEN` – required by `bot.start()`.
  - `BOT_ENVIRONMENT` – controls the command prefix and which TOML file is loaded (`"production"` selects `configs/prod.toml`, any other value uses `configs/dev.toml`).
  - `API_KEY` – forwarded to the `APIService` for authenticated requests.
  - RabbitMQ credentials: `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, and `RABBITMQ_HOST`, all read by `extensions/rabbit` when establishing connections.
  - Optional observability fields such as `SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, and `SENTRY_FEEDBACK_URL` (the compose file passes them through for container deployments).

The TOML schema is defined in `utilities/config.py` and covers guild, role, and channel identifiers. Edit `configs/dev.toml` for development IDs and `configs/prod.toml` for production. The `Genji` constructor reads the appropriate file on startup.

## Local development workflow

1. Install project dependencies with `uv sync --group dev` so the virtual environment includes tooling like Ruff, BasedPyright, and MkDocs.
2. Populate your environment variables (see above) and the `configs/dev.toml` IDs for your Discord sandbox.
3. Run `just run` (which executes `python main.py`) to launch the bot against Discord. Use `just lint` to format and type-check changes before committing.
4. If you run the bot inside Docker, `docker-compose.dev.yml` expects an existing `genji-network` and forwards the same environment variables into the container.

## Deployment checklist

- [ ] Update the appropriate TOML file and secrets for the target environment.
- [ ] Ensure RabbitMQ and the GenjiPK API are reachable from the hosting environment.
- [ ] Build and deploy the container image (or restart the long-running process) with the new code.
- [ ] Monitor Discord logs and Sentry events after rollout.

## Observability

- **Logging:** `setup_logging()` in `main.py` configures log levels, filters noisy Discord messages, and enables DEBUG logs for internal packages when `BOT_ENVIRONMENT` is `"development"`.
- **Sentry:** `main()` initializes Sentry with trace and profile sampling enabled when `SENTRY_DSN` is set.

Document any additional infrastructure requirements (databases, caches, etc.) here as they are introduced so operators have a single source of truth.
