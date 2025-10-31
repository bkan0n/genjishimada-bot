# Newsfeed & Embeds

This section captures the conventions for embeds, components, and presentation patterns across the bot.

## Newsfeed philosophy

- Spotlight the most relevant community events with clear headlines, concise descriptions, and contextual links.
- Keep consistency across updates so returning members can parse new posts quickly.

### Builders & formatters

- `extensions.newsfeed.builders` contains helper functions that assemble rich embeds from domain objects.
- Shared typography and iconography live in `utilities/formatters.py`—reuse these helpers rather than hard-coding strings.
- When referencing runs or player profiles, use the SDK-provided URLs to keep links evergreen.

## Embed guidelines

- **Color palette:** Follow the brand colors defined in `utilities/constants.py` to maintain a cohesive look.
- **Fields:** Prefer fewer than five fields per embed. When more detail is required, collapse information into a Discord view or thread.
- **Timestamps:** Use `discord.utils.utcnow()` for `timestamp` attributes to ensure consistent timezone handling.
- **Footers:** Include actionable context ("Tap to view the full leaderboard") and attribution when pulling from external sources.

## Interactive components

- Views in `utilities/views` encapsulate buttons, selects, and pagination helpers. Extend existing views rather than creating bespoke ones.
- When adding new buttons, supply `custom_id` constants in a dedicated module so they can be traced and invalidated if needed.
- Follow Discord rate-limit guidance—batch updates rather than editing messages multiple times per second.

## Accessibility & localization

- Provide alt text within embeds when using images or attachments.
- Keep copy concise and avoid slang so community translators can localize content easily.
- Use `utilities/i18n` (if present) or prepare for future localization by centralizing user-facing strings.

Document specific embed examples here as new features launch. Including screenshots or JSON payload snippets can help future contributors reproduce the style accurately.
