from __future__ import annotations

import asyncio
import contextlib
import re
from logging import getLogger
from typing import TYPE_CHECKING, Any, Sequence, cast, get_args

import discord
import msgspec
from discord import (
    AllowedMentions,
    AppCommandType,
    Attachment,
    ButtonStyle,
    Color,
    Embed,
    MediaGalleryItem,
    Member,
    Message,
    Role,
    TextChannel,
    TextStyle,
    app_commands,
    ui,
    utils,
)
from genjipk_sdk.models import (
    CompletionPatchDTO,
    CompletionReadDTO,
    MapMasteryCreateDTO,
    MessageQueueCompletionsCreate,
    MessageQueueVerificationChange,
    NewsfeedEvent,
    NewsfeedRecord,
    NewsfeedRole,
    Notification,
    UpvoteUpdateDTO,
)
from genjipk_sdk.models.completions import (
    CompletionVerificationPutDTO,
    FailedAutoverifyMessage,
    QualityUpdateDTO,
    SuspiciousCompletionWriteDTO,
    SuspiciousFlag,
    UpvoteCreateDTO,
)
from genjipk_sdk.models.jobs import ClaimRequest
from genjipk_sdk.models.users import RankDetailReadDTO
from genjipk_sdk.utilities import DIFFICULTY_TO_RANK_MAP, DifficultyAll
from genjipk_sdk.utilities._types import OverwatchCode

from extensions._queue_registry import register_queue_handler
from utilities import transformers
from utilities.base import (
    BaseCog,
    BaseService,
    ConfirmationView,
)
from utilities.completions import (
    CompletionCreateModel,
    CompletionPostVerificationModel,
    CompletionSubmissionModel,
    SuspiciousCompletionModel,
    get_completion_icon_emoji,
    get_completion_icon_url,
    make_ordinal,
)
from utilities.emojis import REJECTED, generate_all_star_rating_strings
from utilities.errors import APIHTTPError, UserFacingError
from utilities.extra import poll_job_until_complete
from utilities.formatter import FilteredFormatter
from utilities.paginator import PaginatorView

if TYPE_CHECKING:
    from aio_pika.abc import AbstractIncomingMessage

    from core.genji import Genji
    from utilities._types import GenjiItx

log = getLogger(__name__)


class RejectionReasonModal(ui.Modal):
    reason = ui.TextInput(label="Reason", style=TextStyle.paragraph)

    def __init__(self) -> None:
        """Initialize the rejection reason modal.

        Sets up a single paragraph-style text input field.
        """
        super().__init__(title="Rejection Reason")

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle the submission of the rejection reason.

        Args:
            itx (GenjiItx): The Discord interaction context.
        """
        await itx.response.send_message(f"Sent the rejection reason as:\n>>> {self.reason.value}", ephemeral=True)


class CompletionsVerificationAcceptButton(ui.Button):
    view: "CompletionVerificationView"

    def __init__(self) -> None:
        """Initialize the Accept button for verifying a submission."""
        super().__init__(style=ButtonStyle.green, label="Accept", custom_id="completions:accept")

    async def callback(self, itx: GenjiItx) -> None:
        """Mark the submission as verified and send a follow-up message.

        Args:
            itx (GenjiItx): The Discord interaction context.
        """
        await itx.response.defer(ephemeral=True, thinking=True)
        for c in self.view.walk_children():
            if isinstance(c, ui.Button):
                c.disabled = True
        if itx.message:
            await itx.message.edit(view=self.view)
        job_status = await self.view.bot.api.verify_completion(
            self.view.data.id, data=CompletionVerificationPutDTO(verified_by=itx.user.id, verified=True, reason=None)
        )
        job = await poll_job_until_complete(itx.client.api, job_status.id)

        if not job:
            log.debug(f"Timed out waiting for job. {job_status.id}")
            await itx.edit_original_response(
                content=(
                    "There was an unknown error while processing. Please do not try again until it has been resolved.\n"
                    f"{self.view.data.code} - {self.view.data.name} - {self.view.data.time}\n"
                )
            )
        elif job.status == "succeeded":
            log.debug(f"Job completed successfully! {job_status.id}")
            await itx.edit_original_response(
                content=(
                    "Successfully verified submission\n"
                    f"{self.view.data.code} - {self.view.data.name} - {self.view.data.time}\n"
                    "Sending verified submission to completions channel."
                )
            )
        else:
            log.debug(f"Job ({job_status.id}) ended with status: {job.status}")
            await itx.edit_original_response(
                content=(
                    "There was an error while processing. Please do not try again until it has been resolved.\n"
                    f"{self.view.data.code} - {self.view.data.name} - {self.view.data.time}\n"
                )
            )

        # TODO: Context Command to reenable the buttons on the view. /repair
        # it should take the message.id as the context command and repair based on location.
        # else present a view with explicit options


class CompletionsVerificationRejectButton(ui.Button):
    view: "CompletionVerificationView"

    def __init__(self) -> None:
        """Initialize the Reject button for denying a submission."""
        super().__init__(style=ButtonStyle.red, label="Reject", custom_id="completions:reject")

    async def callback(self, itx: GenjiItx) -> None:
        """Open a modal for rejection reason and mark the submission as rejected.

        Args:
            itx (GenjiItx): The Discord interaction context.
        """
        modal = RejectionReasonModal()
        await itx.response.send_modal(modal)
        await modal.wait()
        if not modal.reason.value:
            return

        await self.view.bot.api.verify_completion(
            self.view.data.id,
            data=CompletionVerificationPutDTO(verified_by=itx.user.id, verified=False, reason=modal.reason.value),
        )
        await itx.followup.send(
            content=(
                "Successfully rejected submission\n"
                f"{self.view.data.code} - {self.view.data.name} - {self.view.data.time}"
            ),
            ephemeral=True,
        )


class CompletionMessageOrVerificationMessageJumpButton(ui.Button["ViewUserSuspiciousFlagsView"]):
    view: "ViewUserSuspiciousFlagsView"

    def __init__(
        self,
        flag: SuspiciousCompletionModel,
        *,
        guild_id: int,
        verification_channel_id: int,
        completions_channel_id: int,
    ) -> None:
        """Initialize a jump button for a flagged submission.

        Creates a link button that navigates to either the completions channel
        or the verification queue, depending on which ID is set on the flag.

        Args:
            flag: The suspicious completion model providing IDs.
            guild_id: Discord guild ID containing the message.
            verification_channel_id: Channel ID of the verification queue.
            completions_channel_id: Channel ID of the completions channel.
        """
        if flag.message_id:
            url = f"https://discord.com/channels/{guild_id}/{completions_channel_id}/{flag.message_id}"
        else:
            url = f"https://discord.com/channels/{guild_id}/{verification_channel_id}/{flag.verification_id}"
        super().__init__(style=ButtonStyle.link, url=url, label="Jump to message")


class ViewUserSuspiciousFlagsView(PaginatorView[SuspiciousCompletionModel]):
    def __init__(
        self,
        username: str,
        data: Sequence[SuspiciousCompletionModel],
        *,
        guild_id: int,
        verification_channel_id: int,
        completions_channel_id: int,
    ) -> None:
        """Initialize a paginator view for flagged completions.

        Displays suspicious completion flags for a user, split across pages of
        up to 10 entries. Provides jump buttons linking to the relevant
        completion or verification messages.

        Args:
            username: Display name of the flagged user.
            data: Sequence of suspicious completion models to display.
            guild_id: Discord guild ID for constructing message links.
            verification_channel_id: Channel ID for verification queue messages.
            completions_channel_id: Channel ID for completion submission messages.
        """
        self.username = username
        self.guild_id = guild_id
        self.verification_channel_id = verification_channel_id
        self.completions_channel_id = completions_channel_id
        super().__init__(f"Flagged Completions for {self.username}", data, page_size=10)

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build page body for ViewUserSuspiciousFlagsView."""
        data = self.current_page
        res = []
        for flag in data:
            section = (
                ui.Section(
                    ui.TextDisplay(FilteredFormatter(flag).format()),
                    accessory=CompletionMessageOrVerificationMessageJumpButton(
                        flag,
                        guild_id=self.guild_id,
                        verification_channel_id=self.verification_channel_id,
                        completions_channel_id=self.completions_channel_id,
                    ),
                ),
            )
            res.extend(section)
        return res


async def _view_user_suspicious_flags(itx: GenjiItx, username: str, user_id: int) -> None:
    await itx.response.defer(ephemeral=True, thinking=True)

    flags = await itx.client.api.get_suspicious_flags(user_id)

    view = ViewUserSuspiciousFlagsView(
        username,
        flags,
        guild_id=itx.client.config.guild,
        verification_channel_id=itx.client.config.channels.submission.verification_queue,
        completions_channel_id=itx.client.config.channels.submission.completions,
    )
    await itx.edit_original_response(view=view, allowed_mentions=AllowedMentions.none())
    view.original_interaction = itx


class ViewUserSuspiciousFlags(ui.Button):
    view: "CompletionVerificationView"

    def __init__(self) -> None:
        """Init."""
        super().__init__(
            custom_id="verification:completionflagged:view",
            style=ButtonStyle.blurple,
            label="View Flags",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Callback."""
        await _view_user_suspicious_flags(itx, self.view.data.name, self.view.data.user_id)


class CompletionVerificationView(ui.LayoutView):
    def __init__(self, data: CompletionSubmissionModel, bot: Genji) -> None:
        """Initialize the verification view for a completion submission.

        Args:
            data (CompletionSubmissionModel): The submission data to render.
            bot (Genji): The bot instance to use for API access.
        """
        self.data = data
        self.bot = bot
        super().__init__(timeout=None)
        flag_style = ButtonStyle.green if self.data.suspicious else ButtonStyle.red
        accent_color = 0xF04847 if self.data.suspicious else 0x40A258
        self.rebuild_components(flag_style=flag_style, color=accent_color)

    def rebuild_components(
        self,
        *,
        color: Color | int | None = None,
        flag_style: ButtonStyle,
    ) -> None:
        """Rebuild the view contents."""
        self.clear_items()
        container = ui.Container(
            ui.Section(
                ui.TextDisplay(
                    f"New Submission from {self.data.name}\n"
                    + FilteredFormatter(self.data).format()
                    + "\n"
                    + self.data.get_verification_status_text()
                ),
                accessory=ui.Thumbnail(
                    get_completion_icon_url(
                        self.data.completion,
                        self.data.verified,
                        self.data.hypothetical_rank,
                        self.data.hypothetical_medal,
                    )
                ),
            ),
            ui.Separator(),
            ui.MediaGallery(MediaGalleryItem(self.data.screenshot)),
            *(
                ui.Section(
                    ui.TextDisplay(
                        "This user has been marked as suspicious. "
                        "Use this button to view flagged submissions from this user."
                    ),
                    accessory=ViewUserSuspiciousFlags(),
                ),
            )
            if self.data.suspicious
            else (),
            ui.ActionRow(
                CompletionsVerificationAcceptButton(),
                CompletionsVerificationRejectButton(),
            ),
            accent_color=color,
        )
        self.add_item(container)

    async def on_error(self, itx: GenjiItx, error: Exception, item: ui.Item[Any], /) -> None:
        """Handle errors."""
        await itx.client.tree.on_error(itx, cast("app_commands.AppCommandError", error))


class CompletionLikeButton(ui.DynamicItem[ui.Button["CompletionView"]], template=r"upvote:submission:(?P<id>[0-9]+)"):
    view: "CompletionView"

    def __init__(self, completion_id: int) -> None:
        """Initialize the upvote button.

        Args:
            completion_id (int): Completion ID for dynamic creation of the button.
        """
        super().__init__(
            ui.Button(
                style=ButtonStyle.green,
                emoji="🌟",
                label="0",
                custom_id=f"upvote:submission:{completion_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, itx: GenjiItx, item: ui.Button, match: re.Match[str]) -> CompletionLikeButton:
        """Reconstruct an instance from a custom_id match.

        Utility to extract a completion ID from the regex match and use it
        to reinitialize the button or view.

        Args:
            itx: The interaction context for the component.
            item: The UI button that triggered this callback.
            match: Regex match containing the extracted ID.

        Returns:
            CompletionMessageOrVerificationMessageJumpButton: A new instance
            bound to the matched completion ID.
        """
        completion_id = int(match["id"])
        return cls(completion_id)

    async def callback(self, itx: GenjiItx) -> None:
        """Increment the Upvote button and update the view/message.

        Args:
            itx (GenjiItx): The Discord interaction context.
        """
        await itx.response.defer(ephemeral=True)
        assert itx.message
        data = UpvoteCreateDTO(
            user_id=itx.user.id,
            message_id=itx.message.id,
        )
        with contextlib.suppress(Exception):
            data_with_job_status = await itx.client.api.upvote_submission(data)
        new_count = str(data_with_job_status.upvotes)
        if new_count == "None":
            return
        self.item.label = new_count
        await itx.edit_original_response(view=self.view)


class CompletionView(ui.LayoutView):
    def __init__(
        self,
        data: CompletionSubmissionModel,
        *,
        is_dm: bool = False,
        reason: str | None = None,
        verifier_name: str = "",
        playtest_jump_url: str | None = None,
    ) -> None:
        """Initialize the view displaying a completion.

        Args:
            data (CompletionSubmissionModel): The data to display.
            is_dm (bool, optional): Whether this view is shown in a DM. Defaults to False.
            reason (str | None, optional): Rejection reason if applicable. Defaults to None.
            verifier_name (str, optional): Name of the verifier. Defaults to "".
            playtest_jump_url (str): A jump URL if a current playtest exists.
        """
        super().__init__(timeout=None)
        self._data = data
        self.is_dm = is_dm
        self.reason = reason
        self.verifier_name = verifier_name
        self.like_button = CompletionLikeButton(data.id)
        self.playtest_jump_url = playtest_jump_url
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the view contents."""
        if self._data.verified is False and self.reason and self.verifier_name:
            verifier_message = f"-# {REJECTED} Rejected by {self.verifier_name}\n-# **Reason**:{self.reason}"
        elif self._data.verified is True and self.verifier_name:
            verifier_message = f"-# Verified by {self.verifier_name}"
        else:
            verifier_message = ""

        formatted_model = FilteredFormatter(self._data).format()
        playtest_text = (
            (f"\nVisit the playtest thread for this map and vote if you haven't already.\n{self.playtest_jump_url}")
            if self.playtest_jump_url
            else ""
        )
        container = ui.Container(
            ui.Section(
                ui.TextDisplay(
                    f"# New Submission from {self._data.name}\n{verifier_message}\n"
                    f"{formatted_model}\n{self._data.get_verification_status_text()}"
                    f"{playtest_text}"
                ),
                accessory=ui.Thumbnail(
                    get_completion_icon_url(
                        self._data.completion,
                        self._data.verified,
                        self._data.hypothetical_rank,
                        self._data.hypothetical_medal,
                    )
                ),
            ),
            ui.Separator(),
            ui.MediaGallery(MediaGalleryItem(self._data.screenshot)),
            *((ui.ActionRow(self.like_button),) if self._data.verified and not self.is_dm else ()),  # pyright: ignore[reportArgumentType]
        )

        self.add_item(container)

    async def on_error(self, itx: GenjiItx, error: Exception, item: ui.Item[Any], /) -> None:
        """Handle errors."""
        await itx.client.tree.on_error(itx, cast("app_commands.AppCommandError", error))


class CompletionsService(BaseService):
    submission_channel: TextChannel
    verification_channel: TextChannel
    upvote_channel: TextChannel
    verification_views: dict[int, CompletionVerificationView] = {}

    async def _resolve_channels(self) -> None:
        submission_channel = self.bot.get_channel(self.bot.config.channels.submission.completions)
        assert isinstance(submission_channel, TextChannel)
        self.submission_channel = submission_channel

        verification_channel = self.bot.get_channel(self.bot.config.channels.submission.verification_queue)
        assert isinstance(verification_channel, TextChannel)
        self.verification_channel = verification_channel

        upvote_channel = self.bot.get_channel(self.bot.config.channels.submission.upvotes)
        assert isinstance(upvote_channel, TextChannel)
        self.upvote_channel = upvote_channel

    @register_queue_handler("api.completion.autoverification.failed")
    async def _process_autoverification_failed(self, message: AbstractIncomingMessage) -> None:
        try:
            struct = msgspec.json.decode(message.body, type=FailedAutoverifyMessage)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            log.debug("[x] [RabbitMQ] Processing failed autoverify message")
            channel_id = self.bot.config.channels.updates.dlq_alerts
            guild_id = self.bot.config.guild
            guild = self.bot.get_guild(guild_id)
            assert guild
            channel = guild.get_channel(channel_id)
            assert isinstance(channel, TextChannel)

            content = (
                f"`Submitted Code` {struct.submitted_code}\n"
                f"`Submitted Time` {struct.submitted_time}\n"
                f"`User ID` {struct.user_id}\n"
                f"`Extracted Data`\n```\n{struct.extracted}\n```"
            )
            await channel.send(content)

        except Exception as e:
            raise e

    @register_queue_handler("api.completion.upvote")
    async def _process_update_upvote_message(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming RabbitMQ message for completion upvotes.

        Args:
            message (AbstractIncomingMessage): The received message from the queue.
        """
        try:
            struct = msgspec.json.decode(message.body, type=UpvoteUpdateDTO)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            log.debug(f"[x] [RabbitMQ] Processing message: {struct.message_id}")
            await self._handle_upvote_forwarding(struct)

        except Exception as e:
            raise e

    async def _handle_upvote_forwarding(self, data: UpvoteUpdateDTO) -> None:
        """Forward a submission message to the upvote channel.

        Retrieves the partial message by ID from the submission channel and
        forwards it to the configured upvote channel.

        Args:
            data: UpvoteUpdateDTO
        """
        partial_message = self.submission_channel.get_partial_message(data.message_id)
        message = await partial_message.fetch()
        view = ui.LayoutView.from_message(message)
        for c in view.walk_children():
            if isinstance(c, ui.Button):
                log.debug("ITS HAPPENING")
                new_count = str(await self.bot.api.get_upvotes_from_message_id(data.message_id))
                if new_count == "0":
                    return
                c.label = new_count

        await message.edit(view=view)
        await partial_message.forward(self.upvote_channel)

    @register_queue_handler("api.completion.submission")
    async def _process_create_submission_message(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming RabbitMQ message for completion submission.

        Args:
            message (AbstractIncomingMessage): The received message from the queue.
        """
        try:
            struct = msgspec.json.decode(message.body, type=MessageQueueCompletionsCreate)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            log.debug(f"[x] [RabbitMQ] Processing message: {struct.completion_id}")
            await self._handle_verification_queue_message(struct.completion_id)

        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    @register_queue_handler("api.completion.verification")
    async def _process_verification_status_change(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming RabbitMQ message for verification status update.

        Args:
            message (AbstractIncomingMessage): The received message from the queue.
        """
        try:
            struct = msgspec.json.decode(message.body, type=MessageQueueVerificationChange)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            log.debug(f"[x] [RabbitMQ] Processing message: {struct.completion_id}")
            await self._handle_verification_status_change(struct)

        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    async def _handle_verification_queue_message(self, record_id: int) -> None:
        """Create a verification message in the queue.

        Args:
            record_id (int): The record_id of a submission.
        """
        data = await self.bot.api.get_completion_submission(record_id)
        view = CompletionVerificationView(data, self.bot)
        message = await self.verification_channel.send(view=view)
        await self.bot.api.edit_completion(record_id, data=CompletionPatchDTO(verification_id=message.id))
        self.verification_views[message.id] = view

    async def _emit_newsfeed_for_record(self, data: CompletionSubmissionModel) -> None:
        if not (data.video and data.hypothetical_rank):
            raise RuntimeWarning(
                f"A record newsfeed event was emitted when there was no rank or video attached. {data}"
            )
        payload = NewsfeedRecord(
            code=data.code,
            map_name=data.map_name,
            time=data.time,
            video=data.video,
            rank_num=data.hypothetical_rank,
            name=data.name,
            medal=data.hypothetical_medal,
            difficulty=data.difficulty,
        )
        event = NewsfeedEvent(id=None, timestamp=discord.utils.utcnow(), payload=payload, event_type="record")
        await self.bot.api.create_newsfeed(event)

    async def _handle_verification_status_change(self, data: MessageQueueVerificationChange) -> None:
        """Handle the change of verification status for a particular record_id.

        Args:
            data (MessageQueueVerificationChange): Incoming data for verification status change message.
        """
        _data = await self.bot.api.get_completion_submission(data.completion_id)
        completion_data = msgspec.convert(_data, CompletionPostVerificationModel, from_attributes=True)

        guild = self.bot.get_guild(self.bot.config.guild)
        assert guild
        member = guild.get_member(completion_data.user_id)
        verifier = await self.bot.api.get_user(data.verified_by)
        verifier_name = verifier.coalesced_name if verifier and verifier.coalesced_name else "Unknown User"
        should_notify = await self.bot.notifications.should_notify(
            completion_data.user_id, Notification.DM_ON_VERIFICATION
        )
        view = CompletionView(completion_data, verifier_name=verifier_name)
        if data.verified:
            message = await self.submission_channel.send(view=view)
            await self.bot.api.edit_completion(data.completion_id, data=CompletionPatchDTO(message_id=message.id))
            if should_notify and member:
                completion_data = await self.bot.api.get_completion_submission(data.completion_id)
                _view = CompletionView(completion_data, is_dm=True, verifier_name=verifier_name)
                with contextlib.suppress(discord.Forbidden):
                    await member.send(view=_view)

            # Completion
            if completion_data.completion:
                await self.bot.xp.grant_user_xp_of_type(completion_data.user_id, "Completion")
            # World Record
            if not completion_data.completion and completion_data.hypothetical_rank == 1:
                previously_granted = await self.bot.api.check_for_previous_world_record_xp(
                    completion_data.code, completion_data.user_id
                )
                if not previously_granted:
                    await self.bot.xp.grant_user_xp_of_type(completion_data.user_id, "World Record")
                    await self.bot.api.edit_completion(data.completion_id, data=CompletionPatchDTO(wr_xp_check=True))
                await self._emit_newsfeed_for_record(completion_data)
            # Record
            if (
                not completion_data.completion
                and completion_data.hypothetical_rank
                and completion_data.hypothetical_rank > 1
            ):
                await self.bot.xp.grant_user_xp_of_type(completion_data.user_id, "Record")
                await self._emit_newsfeed_for_record(completion_data)

            await self._process_map_mastery(completion_data.user_id)

        elif should_notify and member:
            completion_data = await self.bot.api.get_completion_submission(data.completion_id)
            _view = CompletionView(completion_data, is_dm=True, reason=data.reason, verifier_name=verifier_name)
            with contextlib.suppress(discord.Forbidden):
                await member.send(view=_view)
        if completion_data.verification_id:
            with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
                await (self.verification_channel.get_partial_message(completion_data.verification_id)).delete()
        if member:
            await self.auto_skill_role(member)
        assert completion_data.verification_id
        stoppable_view = self.verification_views.pop(completion_data.verification_id, None)
        if stoppable_view:
            stoppable_view.stop()

    async def _process_map_mastery(self, user_id: int) -> None:
        """Process and update a user's map mastery progress.

        Fetches mastery data for the user, updates records, and posts a
        notification embed when a new valid mastery badge is earned.

        Args:
            user_id: The ID of the user whose mastery should be processed.

        Raises:
            ValueError: If the user cannot be found in the API.
        """
        mastery_data = await self.bot.api.get_map_mastery_data(user_id)
        user_data = await self.bot.api.get_user(user_id)
        if not user_data:
            raise ValueError("User doesn't exist?")
        for m in mastery_data:
            assert m.level
            data = MapMasteryCreateDTO(user_id, m.map_name, m.level)
            updated_mastery = await self.bot.api.update_mastery(data)
            if not updated_mastery or updated_mastery.medal == "Placeholder":
                continue
            nickname = user_data.coalesced_name or "Unknown User"
            map_name = updated_mastery.map_name
            medal = updated_mastery.medal
            m.icon_url
            embed = Embed(
                description=f"{nickname} received the **{map_name} {medal}** Map Mastery badge!",
            )
            embed.set_thumbnail(url=f"https://genji.pk/{m.icon_url}")
            xp_channel = self.guild.get_channel(self.bot.config.channels.updates.xp)
            assert isinstance(xp_channel, TextChannel)
            await self.bot.notifications.notify_channel_default_to_no_ping(
                xp_channel,
                user_id,
                Notification.PING_ON_MASTERY,
                "",
                embed=embed,
            )

    def _determine_skill_rank_roles_to_give(
        self,
        data: list[RankDetailReadDTO],
    ) -> tuple[list[Role], list[Role]]:
        """Determine skill rank roles to give to a member."""
        roles_to_grant = []
        roles_to_remove = []

        guild = self.bot.get_guild(self.bot.config.guild)
        assert guild

        for row in data:
            base_rank_name = DIFFICULTY_TO_RANK_MAP[row.difficulty]
            base_rank = utils.get(guild.roles, name=base_rank_name)

            bronze = utils.get(guild.roles, name=f"{base_rank_name} +")
            silver = utils.get(guild.roles, name=f"{base_rank_name} ++")
            gold = utils.get(guild.roles, name=f"{base_rank_name} +++")

            if row.rank_met:
                roles_to_grant.append(base_rank)
            else:
                roles_to_remove.append(base_rank)

            if row.gold_rank_met:
                roles_to_grant.append(gold)
                roles_to_remove.extend([silver, bronze])
            elif row.silver_rank_met:
                roles_to_grant.append(silver)
                roles_to_remove.extend([gold, bronze])
            elif row.bronze_rank_met:
                roles_to_grant.append(bronze)
                roles_to_remove.extend([gold, silver])
            else:
                roles_to_remove.extend([gold, silver, bronze])

        return roles_to_grant, roles_to_remove

    async def _grant_skill_rank_roles(
        self,
        member: Member,
        roles_to_grant: list[Role],
        roles_to_remove: list[Role],
    ) -> None:
        """Grant skill rank roles to a Discord server Member."""
        new_roles = member.roles
        _actual_added_roles: list[Role] = []
        _actual_removed_roles: list[Role] = []
        for a in roles_to_grant:
            if a not in new_roles:
                new_roles.append(a)
                _actual_added_roles.append(a)
        for r in roles_to_remove:
            if r in new_roles:
                new_roles.remove(r)
                _actual_removed_roles.append(r)

        if set(new_roles) == set(member.roles):
            return

        await member.edit(roles=new_roles)
        response = (
            "🚨***ALERT!***🚨\nYour roles have been updated! If roles have been removed, "
            "it's because a map that you have completed has changed difficulty.\n"
            "Complete more maps to get your roles back!\n"
        )
        if _actual_added_roles:
            response += ", ".join([f"**{x.name}**" for x in _actual_added_roles]) + " has been added.\n"
            user_data = await self.bot.api.get_user(member.id)
            coalesced_name = "Unknown User"
            if user_data:
                coalesced_name = user_data.coalesced_name or "Unknown Name"
            payload = NewsfeedRole(
                user_id=member.id,
                name=coalesced_name,
                added=[x.name for x in _actual_added_roles],
            )
            event_data = NewsfeedEvent(id=None, timestamp=utils.utcnow(), payload=payload, event_type="role")
            await self.bot.api.create_newsfeed(event_data)

        if _actual_removed_roles:
            response += ", ".join([f"**{x.name}**" for x in _actual_removed_roles]) + " has been removed.\n"

        if _actual_added_roles or _actual_removed_roles:
            await self.bot.notifications.notify_dm(
                member.id,
                Notification.DM_ON_SKILL_ROLE_UPDATE,
                response,
            )

    async def auto_skill_role(self, member: Member) -> None:
        """Perform automatic skill roles process."""
        data = await self.bot.api.get_user_rank_data(member.id)
        add, remove = self._determine_skill_rank_roles_to_give(data)
        await self._grant_skill_rank_roles(member, add, remove)

    async def _update_affected_users(self, code: OverwatchCode) -> None:
        """Update roles for users affected by map edits or changes."""
        ids = await self.bot.api.get_affected_users(code)

        if not ids:
            return

        guild = self.bot.get_guild(self.bot.config.guild)
        assert guild
        for _id in ids:
            if member := guild.get_member(_id):
                await self.auto_skill_role(member)


class CompletionLeaderboardFormattable(CompletionReadDTO):
    def to_format_dict(self) -> dict[str, Any]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        _medal = self.legacy_medal if self.legacy else self.medal
        return {
            "Time": f"{self.time} {get_completion_icon_emoji(self.rank, _medal)}",
            "Video": self.video,
            " ": "> -# This is a legacy record" if self.legacy else None,
        }


class CompletionUserFormattable(CompletionReadDTO):
    def to_format_dict(self) -> dict[str, Any]:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "Code": self.code,
            "Difficulty": self.difficulty,
            "Rank": make_ordinal(self.rank) if self.rank else None,
            "Time": f"{self.time} {get_completion_icon_emoji(self.rank, self.medal)}",
            "Video": self.video,
        }


class CompletionMessageLink(ui.Button):
    def __init__(self, guild_id: int, channel_id: int, message_id: int) -> None:
        """Initialize a button linking to a completion submission message.

        Creates a styled link button pointing to the Discord message URL
        for the specified guild, channel, and message IDs.

        Args:
            guild_id: Discord guild ID containing the submission.
            channel_id: Channel ID where the submission message is located.
            message_id: ID of the submission message.
        """
        super().__init__(
            style=ButtonStyle.link,
            label="Go to submission",
            url=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
        )


class CompletionsLeaderboardPaginator(PaginatorView[CompletionLeaderboardFormattable]):
    def __init__(
        self,
        code: OverwatchCode,
        data: list[CompletionLeaderboardFormattable],
        *,
        guild_id: int,
        channel_id: int,
    ) -> None:
        """Initialize a completions leaderboard paginator.

        Sets up a paginated view of completion leaderboard entries for a map,
        including links back to the original submission messages.

        Args:
            code: Overwatch map code the leaderboard belongs to.
            data: List of leaderboard entries to display.
            guild_id: Discord guild ID for constructing message links.
            channel_id: Channel ID for constructing message links.
        """
        self.guild_id = guild_id
        self.channel_id = channel_id
        super().__init__(
            f"Completions - {code}",
            data,
            page_size=10,
        )

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build the UI page body for the current leaderboard page.

        Renders the current page of leaderboard entries as sections,
        each showing rank, player name, formatted details, and a
        button linking to the original submission.

        Returns:
            Sequence[ui.Item]: UI components representing the page.
        """
        sections = []
        for completion in self.current_page:
            ordinal = make_ordinal(completion.rank) if completion.rank else ""
            title = f"{ordinal} - " if ordinal else ""
            formatted = FilteredFormatter(completion).format()
            section = ui.Section(
                ui.TextDisplay(f"**{title}{completion.name}**\n{formatted}"),
                accessory=CompletionMessageLink(self.guild_id, self.channel_id, completion.message_id),
            )
            sections.append(section)

        return sections


class CompletionsUserPaginator(PaginatorView[CompletionUserFormattable]):
    def __init__(
        self,
        username: str,
        data: list[CompletionUserFormattable],
        *,
        guild_id: int,
        channel_id: int,
    ) -> None:
        """Initialize a user completions paginator.

        Sets up a paginated view showing all completions by a user,
        including their “also known as” names and links to the
        original submission messages.

        Args:
            username: The display name of the user whose completions are shown.
            data: List of completion entries for the user.
            guild_id: Discord guild ID for constructing message links.
            channel_id: Channel ID for constructing message links.
        """
        self.guild_id = guild_id
        self.channel_id = channel_id
        super().__init__(f"Completions - {username}", data, page_size=10)

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build the UI page body for the current user's completions.

        Renders the user's completions as sections with formatted
        details and links to submission messages, prefixed with
        their “also known as” names.

        Returns:
            Sequence[ui.Item]: UI components representing the page.
        """
        sections = []
        sections.append(ui.TextDisplay(f"> `Also Known As`{self.current_page[0].also_known_as}"))
        for completion in self.current_page:
            section = ui.Section(
                ui.TextDisplay(FilteredFormatter(completion).format()),
                accessory=CompletionMessageLink(self.guild_id, self.channel_id, completion.message_id),
            )
            sections.append(section)
            sections.append(ui.Separator())

        return sections


class SetSuspiciousModal(ui.Modal):
    flag_type = discord.ui.Label(
        text="Flag Type",
        description="Select the type of suspicious flag.",
        component=discord.ui.Select(
            placeholder="Choose a flag.",
            options=[
                discord.SelectOption(label="Cheating"),
                discord.SelectOption(label="Scripting"),
            ],
        ),
    )
    context = ui.TextInput(label="Context/Reason", style=TextStyle.long)

    def __init__(self, *, message_id: int | None = None, verification_id: int | None = None) -> None:
        """Initialize a modal to flag a submission as suspicious.

        Ensures one of `message_id` or `verification_id` is provided
        to identify the submission being flagged.

        Args:
            message_id: Optional ID of the submission message.
            verification_id: Optional ID of the verification queue message.

        Raises:
            ValueError: If neither message_id nor verification_id is provided.
        """
        if not (message_id or verification_id):
            raise ValueError("One of message_id or verification_id must be set.")
        self.message_id = message_id
        self.verification_id = verification_id
        super().__init__(title="Mark Suspicious Submission")

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle modal submission to add a suspicious flag.

        Validates the flag type, builds a `SuspiciousCompletionWriteDTO`,
        and submits it to the API. Responds to the user with success or
        validation error messages.

        Args:
            itx: The interaction context associated with the modal.
        """
        await itx.response.defer(ephemeral=True, thinking=True)
        if self.flag_type.component.values[0] not in get_args(SuspiciousFlag):  # type: ignore
            await itx.edit_original_response(
                content=f"Flag type must be one of `{', '.join(get_args(SuspiciousFlag))}`",
            )
            return
        data = SuspiciousCompletionWriteDTO(
            message_id=self.message_id,
            verification_id=self.verification_id,
            flag_type=self.flag_type.component.values[0],  # type: ignore
            flagged_by=itx.user.id,
            context=self.context.value,
        )
        await itx.client.api.set_suspicious_flags(data)

        await itx.edit_original_response(content="Adding suspicious flag.")


class CompletionsCog(BaseCog):
    _pending_verification_view_task: asyncio.Task

    async def cog_load(self) -> None:
        """Start the task to restore pending verification views after cog load."""
        self._pending_verification_view_task = asyncio.create_task(self._add_pending_verification_views())
        self.bot.tree.add_command(
            app_commands.ContextMenu(
                name="Mark Suspicious",
                callback=self.mark_submission_as_suspicious_context_command,
                type=AppCommandType.message,
            )
        )
        self.bot.add_dynamic_items(CompletionLikeButton)

    async def _add_pending_verification_views(self) -> None:
        """Restore persistent views for any completions still awaiting verification.

        Args:
            bot (Genji): The bot instance with access to the API and Discord.
        """
        await self.bot.rabbit.wait_until_drained()
        await self.bot.rabbit.wait_until_drained()
        pending = await self.bot.api.get_pending_verifications()
        for p in pending:
            data = await self.bot.api.get_completion_submission(p.id)
            view = CompletionVerificationView(data, self.bot)
            self.bot.add_view(view, message_id=p.verification_id)
            self.bot.completions.verification_views[p.verification_id] = view

    async def mark_submission_as_suspicious_context_command(self, itx: GenjiItx, message: Message) -> None:
        """Mark a submission message as suspicious via context command.

        Opens a modal allowing staff to flag a submission as suspicious if the
        message originates from the verification queue or completions channel.

        Args:
            itx: The interaction context for the invoked command.
            message: The Discord message being flagged.

        Raises:
            Sends an ephemeral error message if the message is not in an
            eligible channel.
        """
        modal = None
        if message.channel.id == itx.client.config.channels.submission.verification_queue:
            modal = SetSuspiciousModal(verification_id=message.id)
        if message.channel.id == itx.client.config.channels.submission.completions:
            modal = SetSuspiciousModal(message_id=message.id)
        if modal is None:
            await itx.response.send_message(
                "You can only mark messages in the verification queue or completions channel.",
                ephemeral=True,
            )
            return
        await itx.response.send_modal(modal)

    @app_commands.command(name="view-flagged-submissions")
    async def view_suspicious_flags(
        self,
        itx: GenjiItx,
        user: app_commands.Transform[int, transformers.UserTransformer],
    ) -> None:
        """View all suspicious flags associated with a user.

        Restricted to Sensei or Mod roles. Fetches the user's suspicious flags
        and displays them using a helper view.

        Args:
            itx: The interaction context for the command.
            user: Target user ID, transformed from input.

        Raises:
            UserFacingError: If the invoking user lacks permission or the target
            user cannot be found.
        """
        assert itx.guild and isinstance(itx.user, Member)
        sensei = itx.guild.get_role(self.bot.config.roles.admin.sensei)
        mod = itx.guild.get_role(self.bot.config.roles.admin.mod)
        if sensei not in itx.user.roles and mod not in itx.user.roles:
            raise UserFacingError("You are not allowed to use this command.")

        user_data = await self.bot.api.get_user(user)
        if not user_data:
            raise UserFacingError(f"This user was not found. User ID: {user}")
        name = user_data.coalesced_name or "Unknown User"
        await _view_user_suspicious_flags(itx, name, user)

    @app_commands.command(name="user-completions")
    async def get_completions_for_user(
        self,
        itx: GenjiItx,
        user: app_commands.Transform[int, transformers.UserTransformer],
        difficulty: DifficultyAll | None = None,
    ) -> None:
        """Get verified completions for a user.

        Fetches the user's completions, optionally filtered by difficulty, and
        displays them in a paginated view.

        Args:
            itx: The interaction context for the command.
            user: Target user ID, transformed from input.
            difficulty: Optional difficulty filter.

        Raises:
            UserFacingError: If the user has no completions.
        """
        await itx.response.defer(ephemeral=True)
        data = await self.bot.api.get_completions_for_user(user, difficulty)
        if not data:
            raise UserFacingError("There are no completions for this user.")
        username = getattr(await self.bot.api.get_user(user), "coalesced_name", None) or "Unknown User"
        view = CompletionsUserPaginator(
            username,
            data,
            guild_id=self.bot.config.guild,
            channel_id=self.bot.config.channels.submission.completions,
        )
        await itx.edit_original_response(view=view)

    @app_commands.command(name="world-records")
    async def get_world_records_for_user(
        self,
        itx: GenjiItx,
        user: app_commands.Transform[int, transformers.UserTransformer],
    ) -> None:
        """Get world records held by a user.

        Fetches the user's verified world records and displays them in a
        paginated view.

        Args:
            itx: The interaction context for the command.
            user: Target user ID, transformed from input.

        Raises:
            UserFacingError: If the user has no world records.
        """
        await itx.response.defer(ephemeral=True)
        data = await self.bot.api.get_world_records_for_user(user)
        if not data:
            raise UserFacingError("There are no world records for this user.")
        username = getattr(await self.bot.api.get_user(user), "coalesced_name", None) or "Unknown User"
        view = CompletionsUserPaginator(
            username,
            data,
            guild_id=self.bot.config.guild,
            channel_id=self.bot.config.channels.submission.completions,
        )
        await itx.edit_original_response(view=view)

    @app_commands.command(name="completions")
    async def get_completion_leaderboard(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeVisibleTransformer],
    ) -> None:
        """View the leaderboard for a specific map code..

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The code being submitted.
        """
        await itx.response.defer(ephemeral=True)
        data = await self.bot.api.get_completions(code)
        if not data:
            raise UserFacingError("There are no completions for this map.")
        view = CompletionsLeaderboardPaginator(
            code,
            data,
            guild_id=self.bot.config.guild,
            channel_id=self.bot.config.channels.submission.completions,
        )
        await itx.edit_original_response(view=view)

    @app_commands.command(name="submit-completion")
    @app_commands.choices(
        quality=[
            app_commands.Choice(
                name=x,
                value=i,
            )
            for i, x in enumerate(generate_all_star_rating_strings(), start=1)
        ]
    )
    async def submit_completion(  # noqa: PLR0913
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeVisibleTransformer],
        time: float,
        quality: app_commands.Choice[int],
        screenshot: Attachment,
        video: str | None,
    ) -> None:
        """Submit a new completion for verification.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The code being submitted.
            time (float): The submitted time.
            screenshot (Attachment): Screenshot URL for the completion.
            quality (int): Quality of the map
            video (str | None): Optional video URL (YouTube preferred).
        """
        channel_id = self.bot.config.channels.submission.completions
        assert itx.channel
        if itx.channel.id != channel_id:
            raise UserFacingError(f"You need to use this command in <#{channel_id}>.")

        ow_usernames = await itx.client.api.get_overwatch_usernames(itx.user.id)

        if not ow_usernames:
            raise UserFacingError(
                "You do not have any Overwatch usernames set. Please use the `/settings` command to set them.\n\n"
                "You can set up to three usernames, if you have alt accounts. The # (descriminator) in your Battle.net"
                "username is not necessary.\n\n"
                "Setting these names allows us to quickly verify your submissions."
            )

        data = CompletionCreateModel(
            code=code,
            user_id=itx.user.id,
            time=time,
            screenshot=screenshot.url,
            video=video,
        )
        message = (
            f"# Does this look correct?\n\n{FilteredFormatter(data).format()}\n\nYou rated this map {quality.name}."
        )

        view = ConfirmationView(message=message, image_url=screenshot.url)
        await itx.response.send_message(view=view, ephemeral=True)
        await view.wait()
        if view.confirmed is not True:
            return

        screenshot_url = await itx.client.api.upload_image(
            await screenshot.read(),
            filename=screenshot.filename,
            content_type=screenshot.content_type or "image/png",
        )
        data.screenshot = screenshot_url
        try:
            _data_with_job_status = await itx.client.api.submit_completion(data)
        except APIHTTPError as e:
            raise UserFacingError(e.error)
        await itx.client.api.set_quality_vote(data.code, QualityUpdateDTO(data.user_id, quality.value))
        data = await self.bot.api.get_completion_submission(_data_with_job_status.completion_id)

        map_data = await self.bot.api.get_map(code=data.code)
        if not map_data.playtest:
            view = CompletionView(data)
            await itx.edit_original_response(view=view)
        else:
            guild_id = self.bot.config.guild
            thread_id = map_data.playtest.thread_id
            jump_url = f"https://discord.com/channels/{guild_id}/{thread_id}"
            view = CompletionView(data, playtest_jump_url=jump_url)
            await itx.edit_original_response(view=view)


async def setup(bot: Genji) -> None:
    """Load the CompletionsCog cog."""
    bot.completions = CompletionsService(bot)
    await bot.add_cog(CompletionsCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the CompletionsCog cog."""
    await bot.remove_cog("CompletionsCog")
