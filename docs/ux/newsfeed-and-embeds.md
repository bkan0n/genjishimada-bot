# Newsfeed & Embeds

Use these guidelines when building embeds, views, and other presentation layers surfaced by the bot.

## Newsfeed builders

- `extensions/newsfeed.py` defines `BaseNewsfeedBuilder` and a family of subclasses (`RecordNewsfeedBuilder`, `AnnouncementNewsfeedBuilder`, etc.) that convert API payloads into `NewsfeedComponentView` instances. Each subclass overrides `build()` to supply titles, imagery, and colours for a specific payload type.
- `NewsfeedService` registers those builders at startup and chooses the correct one based on the payload class. When an `api.newsfeed.create` message arrives, the service fetches the event via `bot.api`, renders the view, and posts it to the configured updates channel.

When adding a new payload from the GenjiPK API, implement a new subclass of `BaseNewsfeedBuilder`, set its `payload_cls`, and let the service auto-register it.

## Embed formatting helpers

- `utilities/formatter.py` includes reusable formatters that turn `msgspec` models or other DTOs into decorated strings for embed fields. Reuse `Formatter` or `FilteredFormatter` to keep typography consistent.
- The thumbnail helper in `utilities/thumbnails.VideoThumbnailService` ensures completion views show a fallback image when media is missing; use it instead of hard-coding URLs.

## Embed guidelines

- **Colour palette:** Prefer the colour factories provided by `discord.Color` in the newsfeed builders so embeds stay visually coherent.
- **Fields:** Avoid overwhelming users with dense field lists. For complex payloads, push detail into dedicated views (e.g., completion verification views) or follow-up messages.
- **Timestamps:** Use `discord.utils.utcnow()` when setting embed timestamps, mirroring the approach used in the newsfeed service when it emits record notifications.

## Accessibility & localisation

- Ensure buttons and selects expose descriptive `custom_id` values. See the completion verification view for examples.
- Keep copy concise and avoid slang so community translators can localise announcements. When possible, surface external URLs through the SDK so links remain stable.

Document additional embed patterns here (with screenshots or payload snippets) whenever new UI surfaces are introduced.
