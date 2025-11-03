from __future__ import annotations

from abc import ABC, abstractmethod
from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Generic, Sequence, Type, TypeVar

import discord
import msgspec
from discord.ui import (
    ActionRow,
    Button,
    Container,
    LayoutView,
    MediaGallery,
    Section,
    Separator,
    TextDisplay,
    Thumbnail,
)
from discord.utils import maybe_coroutine
from genjipk_sdk.models import NewsfeedEvent, NewsfeedQueueMessage
from genjipk_sdk.models.newsfeed import (
    NewsfeedAnnouncement,
    NewsfeedArchive,
    NewsfeedBulkArchive,
    NewsfeedBulkUnarchive,
    NewsfeedGuide,
    NewsfeedLegacyRecord,
    NewsfeedMapEdit,
    NewsfeedNewMap,
    NewsfeedRecord,
    NewsfeedRole,
    NewsfeedUnarchive,
)
from genjipk_sdk.utilities import DIFFICULTY_TO_RANK_MAP, DifficultyAll, convert_extended_difficulty_to_top_level

from extensions._queue_registry import register_queue_handler
from utilities.completions import get_completion_icon_url
from utilities.formatter import FilteredFormatter, FormattableProtocol

if TYPE_CHECKING:
    from aio_pika.abc import AbstractIncomingMessage
    from genjipk_sdk.utilities._types import GuideURL, OverwatchCode, OverwatchMap

    import core
    from extensions.playtest import PlaytestCog

log = getLogger(__name__)


async def setup(bot: core.Genji) -> None:
    """Set up the Newsfeed extension and attach the NewsfeedService to the bot.

    Args:
        bot (core.Genji): The Genji bot instance.
    """
    bot.newsfeed = NewsfeedService(bot)


class VideoLinkButton(Button):
    def __init__(self, url: str) -> None:
        """Initialize a link button that opens the given video URL.

        Args:
            url (str): The URL to link to.
        """
        super().__init__(style=discord.ButtonStyle.link, url=url, label="View Video")


class NewsfeedComponentView(LayoutView):
    def __init__(  # noqa: PLR0913
        self,
        *,
        title: str,
        content: str,
        banner_url: str | None = None,
        thumbnail_url: str | None = None,
        link_url: str | None = None,
        color: discord.Color | None = None,
    ) -> None:
        """Initialize a newsfeed component view for display in Discord.

        Args:
            title (str): Title text to display at the top.
            content (str): Main body content.
            banner_url (str | None): Optional banner image URL.
            thumbnail_url (str | None): Optional thumbnail image URL.
            link_url (str | None): Optional link button URL.
            color (discord.Color | None): Optional accent color for the component.
        """
        super().__init__()
        self.title = title
        self.content = content
        self.banner_url = banner_url
        self.thumbnail_url = thumbnail_url
        self.link_url = link_url
        self.color = color
        self._build()

    def _build(self) -> None:
        """Assemble the Discord UI components for the view."""
        header = TextDisplay(f"# {self.title}")
        body = (
            Section(TextDisplay(self.content), accessory=Thumbnail(self.thumbnail_url))
            if self.thumbnail_url
            else TextDisplay(self.content)
        )
        actions = (ActionRow(VideoLinkButton(self.link_url)),) if self.link_url else ()
        media = (MediaGallery(discord.MediaGalleryItem(self.banner_url)),) if self.banner_url else ()

        container = Container(
            header,
            Separator(),
            body,
            *((Separator(),) if media else ()),
            *media,
            *actions,
            accent_color=self.color,
        )
        self.add_item(container)


def _csv(items: list[str]) -> str:
    """Join a list of strings into a comma-separated string.

    Args:
        items (list[str]): The list of strings.

    Returns:
        str: The comma-separated result.
    """
    return ", ".join(items)


def _codes_block(codes: list[str]) -> str:
    """Format a list of map codes as a Markdown-style bullet list.

    Args:
        codes (list[str]): The codes to format.

    Returns:
        str: The formatted multiline string.
    """
    return f"\n```{'\n'.join(codes)}```"


def _rank_badge_url(diff: DifficultyAll | None) -> str | None:
    """Get the image URL for the badge associated with the difficulty.

    Args:
        diff (DifficultyAll | None): The difficulty level.

    Returns:
        str | None: URL to the badge image, or None if unavailable.
    """
    if not diff:
        return None
    base = "https://bkan0n.com/assets/images/genji_ranks/"
    stripped_diff = convert_extended_difficulty_to_top_level(diff)
    rank = DIFFICULTY_TO_RANK_MAP[stripped_diff].lower()
    return f"{base}{rank}.png"


class RecordFormattable(msgspec.Struct, kw_only=True):
    map_name: OverwatchMap
    code: OverwatchCode
    time: str
    video: GuideURL
    difficulty: DifficultyAll

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Map": self.map_name,
            "Time": self.time,
            "Difficulty": self.difficulty,
        }


class NewMapFormattable(msgspec.Struct, kw_only=True):
    map_name: OverwatchMap
    code: OverwatchCode
    creators: str
    difficulty: DifficultyAll
    official: bool

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Map": self.map_name,
            "Creator(s)": self.creators,
            "Difficulty": self.difficulty,
        }


class ArchiveFormattable(msgspec.Struct, kw_only=True):
    code: OverwatchCode
    map_name: OverwatchMap
    creators: str
    reason: str
    difficulty: DifficultyAll

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Map": self.map_name,
            "Creator(s)": self.creators,
            "Reason": self.reason,
            "Difficulty": self.difficulty,
        }


class BulkActionFormattable(msgspec.Struct, kw_only=True):
    count: str
    reason: str | None
    codes_block: str | None

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Count": self.count,
            "Reason": self.reason,
            "Codes": self.codes_block,
        }


class GuideFormattable(msgspec.Struct, kw_only=True):
    code: OverwatchCode
    author: str
    guide_url: GuideURL

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Author": self.author,
            "Guide": f"[Link]({self.guide_url})",
        }


class LegacyRecordFormattable(msgspec.Struct, kw_only=True):
    code: OverwatchCode
    affected: str
    reason: str

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Affected": self.affected,
            "Reason": self.reason,
        }


class MapEditFormattable(msgspec.Struct, kw_only=True):
    reason: str

    def to_format_dict(self) -> dict[str, str | None]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Reason": self.reason,
        }


P = TypeVar("P")


class BaseNewsfeedBuilder(ABC, Generic[P]):
    event_type: str
    payload_cls: Type[P]

    def __init__(self, bot: core.Genji) -> None:
        """Initialize the builder with access to the bot instance.

        Args:
            bot (core.Genji): Bot instance.
        """
        self.bot = bot

    @abstractmethod
    def build(self, payload: P) -> NewsfeedComponentView | Awaitable[NewsfeedComponentView]:
        """Build a NewsfeedComponentView from the given payload.

        Args:
            payload (P): The payload to format and render.

        Returns:
            NewsfeedComponentView: The rendered view to display.
        """

    def _format(
        self,
        model: FormattableProtocol,
        *,
        value_wrap_character: str = "`",
        filter_fields: Sequence[str] | None = None,
    ) -> str:
        """Format a model into displayable text using FilteredFormatter.

        Args:
            model (FormattableProtocol): The formattable model instance.
            value_wrap_character (str, optional): Character used to wrap values. Defaults to "`".
            filter_fields (Sequence[str] | None): Optional list of fields to include.

        Returns:
            str: The formatted string.
        """
        return FilteredFormatter(
            model,
            value_wrap_character=value_wrap_character,
            filter_fields=filter_fields,
        ).format()


class RecordNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedRecord]):
    event_type = "record"
    payload_cls = NewsfeedRecord

    def build(self, payload: NewsfeedRecord) -> NewsfeedComponentView:
        """Build a newsfeed component view for a new record or medal.

        Args:
            payload (NewsfeedRecord): The newsfeed record payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = RecordFormattable(
            map_name=payload.map_name,
            code=payload.code,
            time=str(payload.time),
            video=payload.video,
            difficulty=payload.difficulty,
        )
        content = self._format(form)
        title = f"{payload.name} set a new World Record!" if payload.rank_num == 1 else f"{payload.name} got a medal!"
        return NewsfeedComponentView(
            title=title,
            content=content,
            thumbnail_url=get_completion_icon_url(
                completion=False,
                verified=True,
                rank=payload.rank_num,
                medal=payload.medal,
            ),
            link_url=payload.video,
            color=discord.Color.yellow(),
        )


class NewMapNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedNewMap]):
    event_type = "new_map"
    payload_cls = NewsfeedNewMap

    def build(self, payload: NewsfeedNewMap) -> NewsfeedComponentView:
        """Build a newsfeed component view for a new map announcement.

        Args:
            payload (NewsfeedNewMap): The new map payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = NewMapFormattable(
            map_name=payload.map_name,
            code=payload.code,
            creators=_csv(payload.creators),
            difficulty=payload.difficulty,
            official=payload.official,
        )
        content = self._format(form)
        title = (
            f"A new {'official' if payload.official else 'unofficial'} {payload.difficulty} "
            f"map on {payload.map_name} has been submitted!"
        )
        return NewsfeedComponentView(
            title=title,
            content=content,
            banner_url=payload.banner_url,
            thumbnail_url=_rank_badge_url(payload.difficulty),
            color=discord.Color.from_str("#111827"),
        )


class ArchiveNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedArchive]):
    event_type = "archive"
    payload_cls = NewsfeedArchive

    def build(self, payload: NewsfeedArchive) -> NewsfeedComponentView:
        """Build a newsfeed view announcing a map has been archived.

        Args:
            payload (NewsfeedArchive): The archive payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = ArchiveFormattable(
            code=payload.code,
            map_name=payload.map_name,
            creators=_csv(payload.creators),
            reason=payload.reason,
            difficulty=payload.difficulty,
        )
        content = self._format(form)
        extra = (
            "\nThis map will not appear in search unless queried by code.\nRecords cannot be submitted while archived."
        )
        return NewsfeedComponentView(
            title=f"{payload.code} has been archived.",
            content=content + extra,
            color=discord.Color.red(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class UnarchiveNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedUnarchive]):
    event_type = "unarchive"
    payload_cls = NewsfeedUnarchive

    def build(self, payload: NewsfeedUnarchive) -> NewsfeedComponentView:
        """Build a newsfeed view announcing a map has been unarchived.

        Args:
            payload (NewsfeedUnarchive): The unarchive payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = ArchiveFormattable(
            code=payload.code,
            map_name=payload.map_name,
            creators=_csv(payload.creators),
            reason=payload.reason,
            difficulty=payload.difficulty,
        )
        content = self._format(form)
        extra = "\nThis map is visible in search and eligible for record submissions."
        return NewsfeedComponentView(
            title=f"{payload.code} has been unarchived.",
            content=content + extra,
            color=discord.Color.green(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class BulkArchiveNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedBulkArchive]):
    event_type = "bulk_archive"
    payload_cls = NewsfeedBulkArchive

    def build(self, payload: NewsfeedBulkArchive) -> NewsfeedComponentView:
        """Build a newsfeed view for bulk map archiving.

        Args:
            payload (NewsfeedBulkArchive): The bulk archive payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = BulkActionFormattable(
            count=str(len(payload.codes)),
            reason=payload.reason,
            codes_block=_codes_block(payload.codes),
        )
        content = self._format(form)
        note = "\nRecords cannot be submitted for archived maps."
        return NewsfeedComponentView(
            title="Multiple maps have been archived.",
            content=content + note,
            color=discord.Color.red(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class BulkUnarchiveNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedBulkUnarchive]):
    event_type = "bulk_unarchive"
    payload_cls = NewsfeedBulkUnarchive

    def build(self, payload: NewsfeedBulkUnarchive) -> NewsfeedComponentView:
        """Build a newsfeed view for bulk map unarchiving.

        Args:
            payload (NewsfeedBulkUnarchive): The bulk unarchive payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = BulkActionFormattable(
            count=str(len(payload.codes)),
            reason=payload.reason,
            codes_block=_codes_block(payload.codes),
        )
        content = self._format(form)
        note = "\nThese maps are visible and eligible for record submissions."
        return NewsfeedComponentView(
            title="Multiple maps have been unarchived.",
            content=content + note,
            color=discord.Color.green(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class GuideNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedGuide]):
    event_type = "guide"
    payload_cls = NewsfeedGuide

    async def build(self, payload: NewsfeedGuide) -> NewsfeedComponentView:
        """Build a newsfeed view for a posted guide.

        Args:
            payload (NewsfeedGuide): The guide newsfeed payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = GuideFormattable(
            code=payload.code,
            author=payload.name,
            guide_url=payload.guide_url,
        )
        content = self._format(form)

        banner_url = await self.bot.thumbnail_service.get_thumbnail(payload.guide_url)
        return NewsfeedComponentView(
            title=f"{payload.name} posted a guide",
            content=content,
            banner_url=banner_url,
            link_url=payload.guide_url,
            color=discord.Color.orange(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class LegacyRecordNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedLegacyRecord]):
    event_type = "legacy_record"
    payload_cls = NewsfeedLegacyRecord

    def build(self, payload: NewsfeedLegacyRecord) -> NewsfeedComponentView:
        """Build a view for a legacy record conversion announcement.

        Args:
            payload (NewsfeedLegacyRecord): The legacy conversion payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        form = LegacyRecordFormattable(
            code=payload.code,
            affected=str(payload.affected_count),
            reason=payload.reason,
        )
        content = self._format(form)
        expl = "\n### Submissions for this map have been marked as legacy due to breaking changes.\n"
        return NewsfeedComponentView(
            title=f"{payload.code} records converted to completions",
            content=content + expl,
            color=discord.Color.red(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class MapEditNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedMapEdit]):
    event_type = "map_edit"
    payload_cls = NewsfeedMapEdit

    def build(self, payload: NewsfeedMapEdit) -> NewsfeedComponentView:
        """Build a newsfeed view summarizing map field edits.

        Args:
            payload (NewsfeedMapEdit): The map edit payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        lines = [f"- `{c.field.capitalize()}` {c.old} → {c.new}" for c in payload.changes]
        block = "\n".join(lines)
        form = MapEditFormattable(reason=payload.reason)
        content = self._format(form) + f"\n{block}"

        return NewsfeedComponentView(
            title=f"{payload.code} was updated",
            content=content,
            color=discord.Color.blurple(),
            thumbnail_url="https://bkan0n.com/assets/images/genji/icons/warning.avif",
        )


class RoleNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedRole]):
    event_type = "role"
    payload_cls = NewsfeedRole

    def build(self, payload: NewsfeedRole) -> NewsfeedComponentView:
        """Build a newsfeed view showing role changes for a user.

        Args:
            payload (NewsfeedRole): The role change payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        guild = self.bot.get_guild(self.bot.config.guild)
        assert guild
        resolved_roles = [discord.utils.find(lambda r: r.name == role, guild.roles) for role in payload.added]
        mentions = "\n".join([role.mention for role in resolved_roles if role])
        return NewsfeedComponentView(
            title=f"Roles updated for {payload.name}",
            content=mentions,
            color=discord.Color.gold(),
            thumbnail_url="https://i.imgur.com/qhcwGOY.png",
        )


class AnnouncementNewsfeedBuilder(BaseNewsfeedBuilder[NewsfeedAnnouncement]):
    event_type = "announcement"
    payload_cls = NewsfeedAnnouncement

    def build(self, payload: NewsfeedAnnouncement) -> NewsfeedComponentView:
        """Build a newsfeed view for a general announcement.

        Args:
            payload (NewsfeedAnnouncement): The announcement payload.

        Returns:
            NewsfeedComponentView: The constructed view.
        """
        return NewsfeedComponentView(
            title=payload.title,
            content=payload.content,
            banner_url=payload.banner_url,
            thumbnail_url=payload.thumbnail_url or "https://i.imgur.com/qhcwGOY.png",
            link_url=payload.url,
            color=discord.Color.blue(),
        )


class NewsfeedService:
    def __init__(self, bot: core.Genji) -> None:
        """Initialize the NewsfeedService.

        Args:
            bot (core.Genji): The Discord bot instance.
        """
        self._bot = bot
        self._registry_by_cls: dict[type, BaseNewsfeedBuilder] = {}
        self._register_newsfeed_item_builders()

    def _register_newsfeed_item_builders(self) -> None:
        """Register all subclasses of BaseNewsfeedBuilder based on their payload types.

        Raises:
            ValueError: If a subclass is missing its `payload_cls` attribute.
        """
        self._registry_by_cls.clear()
        for cls in BaseNewsfeedBuilder.__subclasses__():
            builder = cls(self._bot)
            payload_cls = getattr(builder, "payload_cls", None)
            if payload_cls is None:
                raise ValueError(f"{cls.__name__} missing payload_cls")
            self._registry_by_cls[payload_cls] = builder

    async def _publish_event(
        self,
        event: NewsfeedEvent,
        *,
        channel: discord.TextChannel | discord.Thread | None = None,
    ) -> None:
        """Render and post a NewsfeedEvent to a Discord channel.

        This will ignore announcements made from within Discord itself.

        Args:
            event (NewsfeedEvent): The event containing the payload and type.
            channel (discord.TextChannel | discord.Thread | None): Optional target channel or thread.

        Raises:
            ValueError: If no builder is registered for the payload type.
            RuntimeError: If the resolved channel is not a TextChannel or Thread.
        """
        payload = event.payload
        if getattr(payload, "from_discord", None):
            return

        builder = self._registry_by_cls.get(type(payload))
        if not builder:
            raise ValueError(f"No builder registered for payload class: {type(payload).__name__}")

        view = await maybe_coroutine(builder.build, payload)
        target = channel or self._bot.get_channel(self._bot.config.channels.updates.newsfeed)
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            raise RuntimeError("Resolved channel is not a TextChannel or Thread.")
        await target.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        view.stop()

    @register_queue_handler("api.newsfeed.create")
    async def _process_newsfeed_create(self, message: AbstractIncomingMessage) -> None:
        """Consume and handle a message indicating a new Newsfeed event was created.

        Args:
            message (AbstractIncomingMessage): The incoming message from the queue.
        """
        try:
            qmsg = msgspec.json.decode(message.body, type=NewsfeedQueueMessage)

            if message.headers.get("x-pytest-enabled"):
                log.debug("[RabbitMQ] Pytest message received; skipping publish.")
                return

            log.debug("[RabbitMQ] Processing newsfeed id: %s", qmsg.newsfeed_id)

            event = await self._bot.api.get_newsfeed_event(qmsg.newsfeed_id)
            if event is None:
                log.warning("Newsfeed id %s not found via API.", qmsg.newsfeed_id)
                return

            if event.event_type == "map_edit":
                assert isinstance(event.payload, NewsfeedMapEdit)
                _map = await self._bot.api.get_map(code=event.payload.code)
                if _map.playtesting == "In Progress":
                    guild = self._bot.get_guild(self._bot.config.guild)
                    assert guild and _map.playtest
                    thread = guild.get_thread(_map.playtest.thread_id)
                    await self._publish_event(event, channel=thread)
                    cog: "PlaytestCog" = self._bot.cogs["PlaytestCog"]  # pyright: ignore[reportAssignmentType]
                    view = cog.playtest_views[_map.playtest.thread_id]
                    await view.fetch_data_and_rebuild(self._bot)
                    if not thread:
                        log.warning(f"Was not able to find thread for playtest view. {_map.playtest.thread_id}")
                        return
                    m = thread.get_partial_message(_map.playtest.thread_id)
                    await m.edit(view=view)
                    return
            await self._publish_event(event)

        except Exception:
            log.exception("Failed to process newsfeed create message.")
