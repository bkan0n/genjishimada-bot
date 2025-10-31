# Newsfeed & Embeds

Use these guidelines when building embeds, views, and other presentation layers surfaced by the bot.

## Newsfeed builders

- `extensions/newsfeed.py` defines `BaseNewsfeedBuilder` and a family of subclasses (`RecordNewsfeedBuilder`, `AnnouncementNewsfeedBuilder`, etc.) that convert API payloads into `NewsfeedComponentView` instances. Each subclass overrides `build()` to supply titles, imagery, and colours for a specific payload type.【F:extensions/newsfeed.py†L299-L360】【F:extensions/newsfeed.py†L606-L666】
- `NewsfeedService` registers those builders at startup and chooses the correct one based on the payload class. When an `api.newsfeed.create` message arrives, the service fetches the event via `bot.api`, renders the view, and posts it to the configured updates channel.【F:extensions/newsfeed.py†L678-L776】

When adding a new payload from the GenjiPK API, implement a new subclass of `BaseNewsfeedBuilder`, set its `payload_cls`, and let the service auto-register it.

## Embed formatting helpers

- `utilities/formatter.py` includes reusable formatters that turn `msgspec` models or other DTOs into decorated strings for embed fields. Reuse `Formatter` or `FilteredFormatter` to keep typography consistent.【F:utilities/formatter.py†L1-L73】
- `utilities/views` contains higher-level Discord UI components (for example, guide listings and moderator views). These views are built with the `discord.ui` helpers and can be extended when a new embed requires interactive controls.【F:utilities/views/mod_guides_view.py†L1-L200】
- The thumbnail helper in `utilities/thumbnails.VideoThumbnailService` ensures completion views show a fallback image when media is missing; use it instead of hard-coding URLs.【F:core/genji.py†L39-L47】【F:utilities/thumbnails.py†L178-L214】

## Embed guidelines

- **Colour palette:** Prefer the colour factories provided by `discord.Color` in the newsfeed builders so embeds stay visually coherent.【F:extensions/newsfeed.py†L330-L360】【F:extensions/newsfeed.py†L642-L666】
- **Fields:** Avoid overwhelming users with dense field lists. For complex payloads, push detail into dedicated views (e.g., completion verification views) or follow-up messages.【F:extensions/completions.py†L509-L566】
- **Timestamps:** Use `discord.utils.utcnow()` when setting embed timestamps, mirroring the approach used in the newsfeed service when it emits record notifications.【F:extensions/completions.py†L745-L775】
- **Footers & context:** Provide actionable context such as "Tap to view" copy or status summaries. The existing builders demonstrate how to combine descriptive text with imagery.

## Accessibility & localisation

- Ensure buttons and selects expose descriptive `custom_id` values so they can be revoked or migrated without guessing. See the completion verification view for examples.【F:extensions/completions.py†L485-L566】
- Keep copy concise and avoid slang so community translators can localise announcements. When possible, surface external URLs through the SDK so links remain stable.【F:extensions/api_client.py†L20-L118】

Document additional embed patterns here (with screenshots or payload snippets) whenever new UI surfaces are introduced.
