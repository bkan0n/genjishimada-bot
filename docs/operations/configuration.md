# Configuration & Deployment

Use this page to wire environment variables, TOML configuration, and deployment workflows for the Genji Shimada bot.

## Environment configuration

Runtime settings come from a combination of environment variables and a TOML file loaded at startup.

- `.env`: `main.py` calls `load_dotenv(".env")` so local development can use a plaintext dotenv file.【F:main.py†L14-L23】
- **Core variables:**
  - `DISCORD_TOKEN` – required by `bot.start()`.
  - `BOT_ENVIRONMENT` – controls the command prefix and which TOML file is loaded (`"production"` selects `configs/prod.toml`, any other value uses `configs/dev.toml`).【F:main.py†L56-L80】【F:core/genji.py†L35-L57】
  - `API_KEY` – forwarded to the `APIClient` for authenticated requests.【F:extensions/api_client.py†L159-L170】
  - RabbitMQ credentials: `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, and `RABBITMQ_HOST`, all read by `extensions/rabbit` when establishing connections.【F:extensions/rabbit.py†L19-L49】
  - Optional observability fields such as `SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, and `SENTRY_FEEDBACK_URL` (the compose file passes them through for container deployments).【F:main.py†L35-L63】【F:docker-compose.dev.yml†L4-L18】

The TOML schema is defined in `utilities/config.py` and covers guild, role, and channel identifiers. Edit `configs/dev.toml` for development IDs and `configs/prod.toml` for production. The `Genji` constructor reads the appropriate file on startup.【F:utilities/config.py†L1-L65】【F:configs/dev.toml†L1-L56】【F:core/genji.py†L35-L57】

## Local development workflow

1. Install project dependencies with `uv sync --group dev` so the virtual environment includes tooling like Ruff, BasedPyright, and MkDocs.【F:pyproject.toml†L19-L28】
2. Populate `.env` and the `configs/dev.toml` IDs for your Discord sandbox.
3. Run `just run` (which executes `python main.py`) to launch the bot against Discord. Use `just lint` to format and type-check changes before committing.【F:justfile†L1-L15】
4. If you run the bot inside Docker, `docker-compose.dev.yml` expects an existing `genji-network` and forwards the same environment variables into the container.【F:docker-compose.dev.yml†L1-L20】

## Deployment checklist

- [ ] Update the appropriate TOML file and secrets for the target environment.
- [ ] Ensure RabbitMQ and the GenjiPK API are reachable from the hosting environment.
- [ ] Build and deploy the container image (or restart the long-running process) with the new code.
- [ ] Monitor Discord logs and Sentry events after rollout.

## Observability

- **Logging:** `setup_logging()` in `main.py` configures log levels, filters noisy Discord messages, and enables DEBUG logs for internal packages when `BOT_ENVIRONMENT` is `"development"`.【F:main.py†L24-L54】
- **Sentry:** `main()` initializes Sentry with trace and profile sampling enabled when `SENTRY_DSN` is set.【F:main.py†L56-L70】

Document any additional infrastructure requirements (databases, caches, etc.) here as they are introduced so operators have a single source of truth.
