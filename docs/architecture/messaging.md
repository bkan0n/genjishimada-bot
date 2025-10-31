# Messaging & Queues

The bot consumes RabbitMQ queues to react to events emitted by the GenjiPK platform. Queue handlers live alongside their feature modules and are registered through a shared decorator.

## RabbitMQ integration

- `extensions/_queue_registry.py` provides `register_queue_handler` and `finalize_queue_handlers`. Extensions decorate handler coroutines, and the registry later resolves those functions against the running `Genji` instance.【F:extensions/_queue_registry.py†L1-L64】
- `extensions/rabbit.RabbitClient` opens pooled connections to RabbitMQ, declares the queues (and matching dead-letter queues), wraps handlers for error handling, and tracks startup drain state. The client is created during the `extensions.rabbit` setup hook and started from `Genji.setup_hook`.【F:extensions/rabbit.py†L1-L227】【F:extensions/rabbit.py†L320-L323】【F:core/genji.py†L43-L50】
- Services can call `await bot.rabbit.wait_until_drained()` when they need to delay work until any startup backlog has been processed (for example, before sending verification embeds or playtest updates).【F:extensions/completions.py†L1033-L1043】【F:extensions/playtest.py†L1263-L1280】

## Queue handler lifecycle

1. Decorate an async function or method with `@register_queue_handler("queue-name")` inside the relevant extension module.
2. Parse the message body with `msgspec` models or other validators before touching Discord state.
3. Perform the required Discord or API calls. The wrapper created by `RabbitClient` manages acknowledgements and ensures failures are logged before the message is dead-lettered.【F:extensions/_queue_registry.py†L18-L64】【F:extensions/rabbit.py†L107-L200】

## Queue catalog

| Queue | Handler | Notes |
| --- | --- | --- |
| `api.newsfeed.create` | `extensions.newsfeed.NewsfeedService._on_newsfeed_created` | Fetches the new event from the API and posts it to the configured newsfeed channel, rebuilding playtest views when necessary.【F:extensions/newsfeed.py†L728-L776】
| `api.completion.upvote` | `extensions.completions.CompletionsManager._process_update_upvote_message` | Forwards completion submissions into the upvote channel once an upvote job is processed.【F:extensions/completions.py†L585-L632】
| `api.completion.submission` | `extensions.completions.CompletionsManager._process_create_submission_message` | Builds the verification queue embed for a new completion submission.【F:extensions/completions.py†L633-L668】
| `api.completion.verification` | `extensions.completions.CompletionsManager._process_verification_status_change` | Updates verification state, DMs submitters when appropriate, and can emit newsfeed records.【F:extensions/completions.py†L669-L856】
| `api.playtest.create` | `extensions.playtest.PlaytestManager._process_create_playtest_message` | Creates playtest threads and posts the intake embed when the API schedules a new playtest.【F:extensions/playtest.py†L471-L555】
| `api.playtest.vote.cast` | `extensions.playtest.PlaytestManager._process_vote_cast_message` | Records a new playtest vote and grants XP to the voter.【F:extensions/playtest.py†L556-L618】
| `api.playtest.vote.remove` | `extensions.playtest.PlaytestManager._process_vote_remove_message` | Handles vote removal events to keep Discord state in sync.【F:extensions/playtest.py†L619-L694】
| `api.playtest.approve` | `extensions.playtest.PlaytestManager._process_playtest_approve_message` | Posts approval summaries and cleans up playtest state when a map is approved.【F:extensions/playtest.py†L695-L854】
| `api.playtest.force_accept` | `extensions.playtest.PlaytestManager._process_playtest_force_accept_message` | Mirrors force-accept commands issued from upstream tools.【F:extensions/playtest.py†L855-L1006】
| `api.playtest.force_deny` | `extensions.playtest.PlaytestManager._process_playtest_force_deny_message` | Mirrors force-deny commands issued from upstream tools.【F:extensions/playtest.py†L1007-L1182】
| `api.playtest.reset` | `extensions.playtest.PlaytestManager._process_playtest_reset_message` | Resets playtest runs and refreshes Discord embeds when a session is reset.【F:extensions/playtest.py†L1183-L1262】
| `api.xp.grant` | `extensions.xp.XPManager._process_grant_message` | Applies XP rewards for completions, records, playtests, and other grant types announced by the API.【F:extensions/xp.py†L187-L217】

Keep this table current as new queues are introduced so on-call maintainers can trace message flow quickly.
