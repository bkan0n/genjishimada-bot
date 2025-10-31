# Configuration & Deployment

This page collects operational knowledge for running the Genji Shimada bot in local, staging, and production environments.

## Environment configuration

Configuration is loaded from TOML files in `configs/` alongside environment variables.

1. Copy `configs/example.local.toml` (or the relevant template) to `configs/local.toml`.
2. Populate secrets: Discord bot token, application ID, RabbitMQ credentials, PostgreSQL DSN, Sentry DSN.
3. Set `BOT_ENVIRONMENT` to distinguish between `local`, `staging`, and `production` deployments.

> Keep secrets out of version control. Use 1Password vault entries or GitHub repository secrets for CI/CD.

## Local development workflow

1. Install dependencies using `uv sync`.
2. Start supporting services: `docker compose -f docker-compose.dev.yml up -d rabbitmq postgres`.
3. Launch the bot with `just run` (or `uv run python main.py`).
4. Run `just lint` before pushing changes.

## Deployment checklist

- [ ] Build and push container images with the appropriate tags.
- [ ] Apply database migrations if required.
- [ ] Update configuration secrets in the hosting environment.
- [ ] Restart the bot process (Kubernetes rollout, systemd service, etc.).
- [ ] Monitor Sentry and Discord logs for anomalies.

## Observability

- **Logging:** Structured logging is configured via `core.logging`. Adjust log levels through environment variables.
- **Sentry:** Enabled when `SENTRY_DSN` is present. Wrap long-running tasks with breadcrumbs so errors are traceable.
- **Metrics:** Add instrumentation around queue throughput or message latency if needed using your preferred stack (StatsD, Prometheus).

## Disaster recovery

- Maintain regular PostgreSQL snapshots and RabbitMQ backups.
- Document manual failover procedures for each infrastructure component.
- Keep a runbook that links to this page for quick access during incidents.
