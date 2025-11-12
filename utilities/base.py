from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from functools import wraps
from logging import getLogger
from typing import TYPE_CHECKING, Awaitable, Callable, TypeAlias, cast
from uuid import UUID

import discord
from aio_pika.abc import AbstractIncomingMessage
from discord import ButtonStyle, MediaGalleryItem, NotFound, ui
from discord.app_commands import AppCommandError
from discord.ext import commands

if TYPE_CHECKING:
    import core

    from ._types import GenjiItx

__all__ = ("BaseCog",)

log = getLogger(__name__)


QueueHandler: TypeAlias = Callable[[AbstractIncomingMessage], Awaitable[None]]


class BaseCog(commands.Cog):
    def __init__(self, bot: core.Genji) -> None:
        """Initialize the base cog.

        Args:
            bot (core.Genji): The Discord bot instance.
        """
        self.bot = bot


class BaseView(ui.LayoutView):
    def __init__(self, *, timeout: float | None = 180) -> None:
        """Initialize the base UI view with timeout message.

        Args:
            timeout (float | None): Timeout in seconds before the view becomes inactive.
        """
        super().__init__(timeout=timeout)

        assert self.timeout
        timeout_dt = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=self.timeout), "R")
        self._end_time_string = f"-# ⚠️ This message will expire and become inactive {timeout_dt}."

        self.original_interaction: GenjiItx | None = None
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Override to rebuild the view's interactive components."""

    def disable_children(self) -> None:
        """Disable all interactive children in the view (e.g., buttons, selects)."""
        for child in self.walk_children():
            if isinstance(child, (ui.Button, ui.Select)):
                child.disabled = True

    async def on_timeout(self) -> None:
        """Disable the view and edit the message if the view times out."""
        self._end_time_string = "-# ⚠️ This message has expired."
        self.rebuild_components()
        self.disable_children()
        if self.original_interaction:
            with contextlib.suppress(NotFound):
                resp = await self.original_interaction.original_response()
                await resp.edit(view=self)
                return
        cls = type(self)
        raise RuntimeWarning(f"No original_interaction was associated with {cls.__module__}.{cls.__qualname__}.")


class ConfirmationButton(ui.Button["ConfirmationView"]):
    view: "ConfirmationView"

    def __init__(self) -> None:
        """Initialize the confirmation button."""
        super().__init__(label="Confirm", style=ButtonStyle.green)

    async def callback(self, itx: GenjiItx) -> None:
        """Handle confirmation click.

        Args:
            itx (GenjiItx): The interaction context.

        Raises:
            Exception: Propagates any exceptions from confirm_callback if unhandled.
        """
        self.view.disable_children()
        await itx.response.edit_message(view=self.view)
        await itx.followup.send("Confirmed.", ephemeral=True)
        self.view.confirmed = True
        if self.view.confirm_callback:
            await discord.utils.maybe_coroutine(self.view.confirm_callback)
        self.view.stop()


class ConfirmationCancelButton(ui.Button["ConfirmationView"]):
    view: "ConfirmationView"

    def __init__(self) -> None:
        """Initialize the cancel button."""
        super().__init__(label="Cancel", style=ButtonStyle.red)

    async def callback(self, itx: GenjiItx) -> None:
        """Handle cancel button click.

        Args:
            itx (GenjiItx): The interaction context.
        """
        self.view.disable_children()
        await itx.response.edit_message(view=self.view)
        await itx.followup.send("Cancelled.", ephemeral=True)
        await self.view.on_timeout()


class ConfirmationView(BaseView):
    def __init__(
        self,
        message: str,
        callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = None,
        image_url: str | None = None,
    ) -> None:
        """Initialize a confirmation view.

        Args:
            message (str): The message to display.
            callback (Callable | Awaitable | None, optional): An optional confirmation callback.
            image_url (str | None, optional): Optional URL to an image or banner.
        """
        self.message: str = message
        self.confirm_callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = callback
        self.confirmed: bool | None = None
        self.image_url: str | None = image_url
        super().__init__()

    def rebuild_components(self) -> None:
        """Rebuild the confirmation view components."""
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay(self.message),
            *((ui.MediaGallery(MediaGalleryItem(self.image_url)),) if self.image_url else ()),
            ui.ActionRow(
                ConfirmationButton(),
                ConfirmationCancelButton(),
            ),
            ui.Separator(),
            ui.TextDisplay(f"# {self._end_time_string}"),
        )
        self.add_item(container)

    async def on_error(self, itx: GenjiItx, error: Exception, item: ui.Button | ui.Select, /) -> None:
        await itx.client.tree.on_error(itx, cast("AppCommandError", error))


class BaseService:
    """Abstract base class for Discord services tied to a shared guild.

    Provides common logic for asynchronously initializing the Discord guild
    and defers channel resolution to subclasses via a hook method.
    """

    guild: discord.Guild
    _guild_and_channel_set: bool = False
    _set_attrs_task: asyncio.Task

    def __init__(self, bot: core.Genji) -> None:
        """Initializes the base service.

        Sets up asynchronous initialization of the shared Discord guild
        and invokes the subclass-defined channel resolution logic.

        Args:
            bot (core.Genji): The bot instance used for Discord access and API communication.
        """
        self.bot = bot
        self._ensure_guild_and_channel_lock = asyncio.Lock()
        self._set_attrs_task = asyncio.create_task(self._ensure_guild_and_channel())

    async def _ensure_guild_and_channel(self) -> None:
        """Ensure the shared Discord guild and service channels are initialized.

        Raises:
            AssertionError: If the configured guild cannot be retrieved.
        """
        if self._guild_and_channel_set:
            return

        await self.bot.wait_until_ready()

        async with self._ensure_guild_and_channel_lock:
            if self._guild_and_channel_set:
                return

            guild = self.bot.get_guild(self.bot.config.guild)
            assert guild is not None
            self.guild = guild

            await self._resolve_channels()
            self._guild_and_channel_set = True

    async def _resolve_channels(self) -> None:
        """Resolves service-specific Discord channels.

        This method must be overridden by subclasses to fetch and assign the
        channels relevant to the specific service.

        Raises:
            NotImplementedError: If not implemented by the subclass.
        """
        raise NotImplementedError("Subclasses must implement _resolve_channels")

    async def _job_patch(self, job_id: UUID, payload: dict) -> None:
        """Best-effort: never raise so queue flow isn't blocked."""
        try:
            await self.bot.api.update_job(job_id=job_id, **payload)  # adapt args as needed
        except Exception as e:
            log.warning(f"[jobs] PATCH failed for {job_id}: {e}")

    def _wrap_job_status(self, fn: QueueHandler) -> QueueHandler:
        """Wrap a queue handler to report job status using message.correlation_id.

        - Does NOT touch ack/nack or `message.process()`.
        - Re-raises exceptions so your existing retry/nack semantics stay intact.
        """

        @wraps(fn)
        async def _inner(message: AbstractIncomingMessage) -> None:
            job_id = UUID(message.correlation_id or "")
            if job_id:
                await self._job_patch(job_id, {"status": "processing"})
            try:
                await fn(message)
                if job_id:
                    await self._job_patch(job_id, {"status": "succeeded"})
            except Exception as e:
                if job_id:
                    await self._job_patch(
                        job_id,
                        {
                            "status": "failed",
                            "error_code": str(getattr(e, "code", "BOT_ERROR")),
                            "error_msg": str(e)[:300],
                        },
                    )
                raise

        return cast(QueueHandler, _inner)


class BaseLoadingView(ui.LayoutView):
    def __init__(self) -> None:
        """Initialize a loading screen view with a Genji-themed spinner image."""
        super().__init__()
        container = ui.Container(
            ui.MediaGallery(MediaGalleryItem("https://bkan0n.com/assets/images/genji/icons/loading.avif"))
        )
        self.add_item(container)
        self.stop()
