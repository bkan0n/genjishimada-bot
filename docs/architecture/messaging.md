# Messaging & Queues

The bot consumes RabbitMQ queues to react to events emitted by the Genji Parkour API. Queue handlers live alongside their feature modules and are registered through a shared decorator.

## RabbitMQ integration

- `extensions/_queue_registry.py` provides `register_queue_handler` and `finalize_queue_handlers`. Extensions decorate handler coroutines, and the registry later resolves those functions against the running `Genji` instance.
- `extensions/rabbit.RabbitService` opens pooled connections to RabbitMQ, declares the queues (and matching dead-letter queues), wraps handlers for error handling, and tracks startup drain state. The client is created during the `extensions.rabbit` setup hook and started from `Genji.setup_hook`.
- Services can call `await bot.rabbit.wait_until_drained()` when they need to delay work until any startup backlog has been processed (for example, before sending verification embeds or playtest updates).

## Queue handler lifecycle

1. Decorate an async function or method with `@register_queue_handler("queue-name")` inside the relevant extension module.
2. Parse the message body with `msgspec` models or other validators before touching Discord state.
3. Perform the required Discord or API calls. The wrapper created by `RabbitService` manages acknowledgements and ensures failures are logged before the message is dead-lettered.

## Queue catalog

| Queue | Handler | Notes |
| --- | --- | --- |
| `api.newsfeed.create` | `NewsfeedService._on_newsfeed_created` | Fetches the new event from the API and posts it to the configured newsfeed channel, rebuilding playtest views when necessary.
| `api.completion.upvote` | `CompletionsService._process_update_upvote_message` | Forwards completion submissions into the upvote channel once an upvote job is processed.
| `api.completion.submission` | `CompletionsService._process_create_submission_message` | Builds the verification queue embed for a new completion submission.
| `api.completion.verification` | `CompletionsService._process_verification_status_change` | Updates verification state, DMs submitters when appropriate, and can emit newsfeed records.
| `api.playtest.create` | `PlaytestService._process_create_playtest_message` | Creates playtest threads and posts the intake embed when the API schedules a new playtest.
| `api.playtest.vote.cast` | `PlaytestService._process_vote_cast_message` | Records a new playtest vote and grants XP to the voter.
| `api.playtest.vote.remove` | `PlaytestService._process_vote_remove_message` | Handles vote removal events to keep Discord state in sync.
| `api.playtest.approve` | `PlaytestService._process_playtest_approve_message` | Posts approval summaries and cleans up playtest state when a map is approved.
| `api.playtest.force_accept` | `PlaytestService._process_playtest_force_accept_message` | Mirrors force-accept commands issued from upstream tools.
| `api.playtest.force_deny` | `PlaytestService._process_playtest_force_deny_message` | Mirrors force-deny commands issued from upstream tools.
| `api.playtest.reset` | `PlaytestService._process_playtest_reset_message` | Resets playtest runs and refreshes Discord embeds when a session is reset.
| `api.xp.grant` | `XPService._process_grant_message` | Applies XP rewards for completions, records, playtests, and other grant types announced by the API.

Keep this table current as new queues are introduced so on-call maintainers can trace message flow quickly.
