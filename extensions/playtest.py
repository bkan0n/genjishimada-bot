from __future__ import annotations

import asyncio
import contextlib
import os
from logging import getLogger
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, cast

import discord
import msgspec
from discord.app_commands import AppCommandError
from discord.ui import Item
from genjipk_sdk.models import (
    MapPatchDTO,
    MapReadPartialDTO,
    MessageQueueCreatePlaytest,
    NewsfeedEvent,
    NewsfeedNewMap,
    Notification,
    PlaytestApproveCreate,
    PlaytestApproveMQ,
    PlaytestAssociateIDThread,
    PlaytestForceAcceptCreate,
    PlaytestForceAcceptMQ,
    PlaytestForceDenyCreate,
    PlaytestForceDenyMQ,
    PlaytestPatchDTO,
    PlaytestResetCreate,
    PlaytestResetMQ,
    PlaytestVote,
    PlaytestVoteCastMQ,
    PlaytestVoteRemovedMQ,
)
from genjipk_sdk.models.jobs import ClaimRequest
from genjipk_sdk.utilities import (
    DIFFICULTY_MIDPOINTS,
    DIFFICULTY_RANGES_ALL,
    DifficultyAll,
    convert_extended_difficulty_to_top_level,
    convert_raw_difficulty_to_difficulty_all,
)

from extensions._queue_registry import register_queue_handler
from utilities import BaseCog, BaseService
from utilities.base import ConfirmationView
from utilities.errors import UserFacingError
from utilities.formatter import FilteredFormatter
from utilities.maps import MapModel

if TYPE_CHECKING:
    from aio_pika.abc import AbstractIncomingMessage
    from genjipk_sdk.utilities._types import PlaytestStatus

    import core
    from utilities._types import GenjiItx

GENJI_API_KEY: str = os.getenv("GENJI_API_KEY", "")

log = getLogger(__name__)


_disabled_notifications_alert = (
    "-# Map creator, you have disabled this type of notfication. "
    "Please change your settings if you wish to receive this notification through DMs."
)


class PlaytestService(BaseService):
    playtest_channel: discord.ForumChannel
    verification_channel: discord.TextChannel

    def __init__(self, bot: core.Genji) -> None:
        """Initialize the playtest manager.

        Args:
            bot (core.Genji): Bot instance for Discord and API access.
        """
        super().__init__(bot=bot)

    async def _resolve_channels(self) -> None:
        """Resolve and cache the playtest forum channel.

        Asserts the configured channel exists and stores it for later use.
        """
        playtest_channel = self.bot.get_channel(self.bot.config.channels.submission.playtest)
        assert isinstance(playtest_channel, discord.ForumChannel)
        self.playtest_channel = playtest_channel

        verification_channel = self.bot.get_channel(self.bot.config.channels.submission.verification_queue)
        assert isinstance(verification_channel, discord.TextChannel)
        self.verification_channel = verification_channel

    def _get_forum_tag(self, value: str) -> discord.ForumTag:
        """Retrieve a forum tag by its name.

        Args:
            value (str): The name of the tag to search for.

        Returns:
            discord.ForumTag: The tag object matching the given name.

        Raises:
            ValueError: If no tag with the given name exists in the playtest forum.
        """
        tags = self.playtest_channel.available_tags
        for tag in tags:
            if tag.name == value:
                return tag
        raise ValueError(f"Unknown tag: {value}")

    async def _add_playtest(self, partial_data: MapReadPartialDTO, playtest_id: int) -> None:
        """Create a new playtest forum thread and populate it with metadata.

        This method:
        - Ensures the guild and playtest channel are available.
        - Creates a forum thread with relevant difficulty and status tags.
        - Inserts a new playtest entry into the database.
        - Reveals the associated map if it was hidden.
        - Edits the forum thread message with components and an attached plot.

        Args:
            partial_data (MapReadPartialDTO): Partial map data required to create the playtest.
            playtest_id (int): The ID of the playtest meta.
        """
        await self._ensure_guild_and_channel()

        tag = self._get_forum_tag(convert_extended_difficulty_to_top_level(partial_data.difficulty))
        open_tag = self._get_forum_tag("Open")
        thread, message = await self.playtest_channel.create_thread(
            name=partial_data.thread_name,
            content="Loading...",
            reason="Playtest test created",
            applied_tags=[open_tag, tag],
        )

        metadata = PlaytestAssociateIDThread(
            playtest_id=playtest_id,
            thread_id=thread.id,
        )
        await self.bot.api.associate_playtest_meta(metadata)
        await self.bot.api.edit_map(code=partial_data.code, data=MapPatchDTO(hidden=False))
        playtest_data = await self.bot.api.get_map(playtest_thread_id=thread.id)
        file = await self.bot.api.get_plot_file(code=playtest_data.code)

        cog: "PlaytestCog" = self.bot.cogs["PlaytestCog"]  # pyright: ignore[reportAssignmentType]
        previous_view = cog.playtest_views.get(playtest_id, None)
        if previous_view:
            previous_view.stop()

        view = PlaytestComponentsV2View(data=playtest_data, thread_id=thread.id)

        cog.playtest_views[playtest_id] = view

        await message.edit(content=None, view=view, attachments=[file])
        await thread.send(f"<@{playtest_data.primary_creator_id}>")

    @register_queue_handler("api.playtest.create")
    async def _process_create_playtest_message(self, message: AbstractIncomingMessage) -> None:
        """Handle a RabbitMQ message to create a playtest thread.

        Decodes the message and initiates the playtest creation process via
        `add_playtest`.

        Args:
            message (AbstractIncomingMessage): The message containing the playtest creation payload.

        Raises:
            msgspec.ValidationError: If the message body cannot be decoded to the expected format.
        """
        try:
            struct = msgspec.json.decode(message.body, type=MessageQueueCreatePlaytest)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            log.debug(f"[x] [RabbitMQ] Processing message: {struct.code}")

            model = await self.bot.api.get_partial_map(struct.code)
            await self._add_playtest(model, struct.playtest_id)
        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    async def _set_playtesting_status(self, *, code: str, status: PlaytestStatus) -> None:
        """Set the map's playtesting status via API.

        Args:
            code: Map code.
            status: New playtest status.
        """
        await self.bot.api.edit_map(code, MapPatchDTO(playtesting=status))

    async def _update_map_difficulty(self, *, code: str, difficulty: DifficultyAll) -> None:
        """Update a map's difficulty via API.

        Args:
            code: Map code.
            difficulty: Difficulty value to set.
        """
        await self.bot.api.edit_map(code, MapPatchDTO(difficulty=difficulty))

    async def _alert_creator(self, *, creator_user_id: int, message: str) -> bool:
        """DM the primary creator about an action.

        Args:
            creator_user_id: Discord user ID of the creator.
            message: Message content.

        Returns:
            True if DM delivered; False if DMs are closed/disabled.
        """
        return await self.bot.notifications.notify_dm(creator_user_id, Notification.DM_ON_PLAYTEST_ALERTS, message)

    async def _post_newsfeed_new_map(self, *, code: str) -> None:
        """Create a 'new/approved map' newsfeed entry via API.

        Args:
            code: Map code.
        """
        m = await self.bot.api.get_map(code=code)
        payload = NewsfeedNewMap(
            code=m.code,
            map_name=m.map_name,
            difficulty=m.difficulty,
            banner_url=m.map_banner,
            creators=[x.name for x in m.creators],
            official=m.official,
            title=m.title,
        )
        event = NewsfeedEvent(id=0, timestamp=discord.utils.utcnow(), payload=payload, event_type="new_map")
        await self.bot.api.create_newsfeed(event)

    async def _fetch_thread(self, thread_id: int) -> discord.Thread:
        """Resolve a thread by ID.

        Args:
            thread_id: Thread channel ID.

        Returns:
            The Discord thread instance.

        Raises:
            discord.NotFound/HTTPException: If the thread can't be fetched.
        """
        ch = self.verification_channel.get_thread(thread_id)

        if isinstance(ch, discord.Thread):
            return ch
        return await self.bot.fetch_channel(thread_id)  # type: ignore[return-value]

    async def _edit_thread_tags_close(self, *, thread_id: int, cancelled: bool) -> None:
        """Archive/lock a thread and swap tags to Complete/Cancelled.

        Args:
            thread_id: Thread channel ID.
            cancelled: If True, apply 'Cancelled'; else apply 'Complete'.
        """
        log.debug("Edit thread tags close starting")

        thread = await self._fetch_thread(thread_id)
        open_tag = self._get_forum_tag("Open")
        final_tag = self._get_forum_tag("Cancelled" if cancelled else "Complete")
        applied_tags = set(thread.applied_tags) | {final_tag}
        applied_tags.discard(open_tag)
        await thread.edit(archived=True, locked=True, applied_tags=list(applied_tags))

    async def _delete_verification_message_if_any(self, *, thread_id: int) -> None:
        """Remove a pending verification message from the queue channel, if present.

        Args:
            thread_id: Playtest thread ID.
        """
        playtest = await self.bot.api.get_playtest(thread_id)
        if playtest.verification_id is None:
            return

        msg = self.verification_channel.get_partial_message(playtest.verification_id)
        with contextlib.suppress(discord.Forbidden, discord.NotFound, discord.HTTPException):
            await msg.delete()

    async def _update_plot_image_on_playtest_message(self, *, thread_id: int) -> None:
        """Re-render the plot and replace the attachment on the root playtest message.

        This assumes you persisted the root message_id for the thread in playtest meta.

        Args:
            thread_id: Playtest thread ID.
        """
        image = await self.bot.api.get_plot_file(thread_id=thread_id)
        thread = await self._fetch_thread(thread_id)
        message = await thread.fetch_message(thread_id)
        await message.edit(attachments=[image])

    async def _grant_xp_upon_successful_playtest(self, thread_id: int) -> None:
        votes = await self.bot.api.get_all_votes(thread_id)
        for vote in votes.votes:
            await self.bot.xp.grant_user_xp_of_type(vote.user_id, "Playtest")

    async def _approve_playtest(
        self,
        *,
        code: str,
        thread_id: int,
        verifier_id: int,
        difficulty: DifficultyAll,
        primary_creator_id: int | None,
    ) -> None:
        """Perform Discord-side effects for an approved playtest.

        DMs the creator (or posts in-thread on DM failure), posts a newsfeed
        event, closes the thread with final tags, removes any verification
        queue message, and refreshes the plot.

        Args:
            code (str): Map code.
            thread_id (int): Playtest thread ID.
            difficulty (DifficultyAll): Approved difficulty.
            verifier_id (int): Discord user ID of the verifier.
            primary_creator_id (int | None): Primary creator to notify, if any.
        """
        if primary_creator_id is not None:
            msg = f"Your map ({code}) has been **accepted** by <@{verifier_id}> with a difficulty of {difficulty}."
            delivered = await self._alert_creator(creator_user_id=primary_creator_id, message=msg)
            if not delivered:
                thread = await self._fetch_thread(thread_id)
                await thread.send(msg)
        await self.bot.api.edit_map(code, MapPatchDTO(difficulty=difficulty))
        await self._grant_xp_upon_successful_playtest(thread_id)
        await self._post_newsfeed_new_map(code=code)
        await self._edit_thread_tags_close(thread_id=thread_id, cancelled=False)
        await self._delete_verification_message_if_any(thread_id=thread_id)

    async def _force_accept_playtest(
        self,
        *,
        code: str,
        thread_id: int,
        difficulty: DifficultyAll,
        verifier_id: int,
        notify_primary_creator_id: int | None,
    ) -> None:
        """Perform Discord-side effects for a force-accepted playtest.

        Args:
            code (str): Map code.
            thread_id (int): Playtest thread ID.
            difficulty (DifficultyAll): Forced difficulty.
            verifier_id (int): Discord user ID of the verifier.
            notify_primary_creator_id (int | None): Primary creator to notify, if any.
        """
        if notify_primary_creator_id is not None:
            msg = (
                f"Your map ({code}) has been **force accepted** by <@{verifier_id}> with a difficulty of {difficulty}."
            )
            delivered = await self._alert_creator(creator_user_id=notify_primary_creator_id, message=msg)
            if not delivered:
                thread = await self._fetch_thread(thread_id)
                await thread.send(msg)
        await self.bot.api.edit_map(code, MapPatchDTO(difficulty=difficulty))
        await self._grant_xp_upon_successful_playtest(thread_id)
        await self._post_newsfeed_new_map(code=code)
        await self._edit_thread_tags_close(thread_id=thread_id, cancelled=False)
        await self._delete_verification_message_if_any(thread_id=thread_id)

    async def _force_deny_playtest(
        self,
        *,
        code: str,
        thread_id: int,
        verifier_id: int,
        reason: str,
        notify_primary_creator_id: int | None,
    ) -> None:
        """Perform Discord-side effects for a force-denied playtest.

        Args:
            code (str): Map code.
            thread_id (int): Playtest thread ID.
            verifier_id (int): Discord user ID of the verifier.
            reason (str): Reason for denial.
            notify_primary_creator_id (int | None): Primary creator to notify, if any.
        """
        if notify_primary_creator_id is not None:
            msg = f"Your map ({code}) has been **denied** by <@{verifier_id}>.\n\nReason: {reason}"
            delivered = await self._alert_creator(creator_user_id=notify_primary_creator_id, message=msg)
            if not delivered:
                thread = await self._fetch_thread(thread_id)
                await thread.send(msg)
        await self._edit_thread_tags_close(thread_id=thread_id, cancelled=True)
        await self._delete_verification_message_if_any(thread_id=thread_id)

    async def _reset_playtest_votes_and_completions(  # noqa: PLR0913
        self,
        *,
        code: str,
        thread_id: int,
        verifier_id: int,
        reason: str,
        remove_votes: bool,
        remove_completions: bool,
        notify_primary_creator_id: int | None,
    ) -> None:
        """Perform Discord-side effects for a reset action.

        Refreshes the plot, notifies the creator (fallback to thread + @here),
        and removes any verification-queue message.

        Args:
            code (str): Map code.
            thread_id (int): Playtest thread ID.
            verifier_id (int): Discord user ID of the verifier.
            reason (str): Reason for the reset.
            remove_votes (bool): Whether votes were removed in the API.
            remove_completions (bool): Whether completions were removed in the API.
            notify_primary_creator_id (int | None): Primary creator to notify, if any.
        """
        await self._update_plot_image_on_playtest_message(thread_id=thread_id)

        msg_prefix = (
            "All votes and completions have been removed."
            if remove_completions
            else "All votes have been removed. Completions were NOT removed."
        )
        full_msg = f"Your map ({code}) has been **reset** by <@{verifier_id}>. {msg_prefix}\n\nReason: {reason}"

        thread = await self._fetch_thread(thread_id)
        if notify_primary_creator_id is not None:
            delivered = await self._alert_creator(creator_user_id=notify_primary_creator_id, message=full_msg)
            if not delivered:
                await thread.send(full_msg)
                await thread.send("@here")
        else:
            await thread.send(full_msg)
            await thread.send("@here")

        await self._delete_verification_message_if_any(thread_id=thread_id)

    async def _announce_vote_in_thread(self, *, thread_id: int, voter_id: int, label: str | None) -> None:
        """Post a vote announcement (or removal) into the playtest thread.

        Args:
            thread_id (int): Playtest thread ID.
            voter_id (int): Discord user ID of the voter.
            label (str | None): Difficulty label, or None when removing a vote.
        """
        thread = await self._fetch_thread(thread_id)
        if label:
            await thread.send(f"<@{voter_id}> voted **{label}**")
        else:
            await thread.send(f"<@{voter_id}> removed their vote")

    async def _rebuild_view_and_plot(self, *, thread_id: int) -> None:
        """Refresh the playtest view and replace the plot attachment.

        If the submission becomes finalizable, notifies creators in-thread.

        Args:
            thread_id (int): Playtest thread ID.
        """
        playtest_data = await self.bot.api.get_map(playtest_thread_id=thread_id)
        cog: "PlaytestCog" = self.bot.cogs["PlaytestCog"]  # pyright: ignore[reportAssignmentType]

        previous_view = cog.playtest_views.get(thread_id, None)
        view = PlaytestComponentsV2View(data=playtest_data, thread_id=thread_id)
        if previous_view:
            previous_view.stop()
            view.data.override_finalize = previous_view.data.override_finalize
            view.rebuild_components()
        cog.playtest_views[thread_id] = view

        file = await self.bot.api.get_plot_file(thread_id=thread_id)
        thread = await self._fetch_thread(thread_id)
        msg = thread.get_partial_message(thread_id)
        await msg.edit(view=view, attachments=[file])
        assert playtest_data.playtest
        if (
            playtest_data.finalizable
            and not view.data.override_finalize
            and playtest_data.playtest.vote_count == playtest_data.playtest_threshold
        ):
            guild = thread.guild if thread_id else (await self._fetch_thread(thread_id)).guild
            mentions = []
            for c in playtest_data.creators:
                m = guild.get_member(c.id)
                mentions.append(m.mention if m else c.name)
            await (await self._fetch_thread(thread_id)).send(
                f"{', '.join(mentions)} — The finalize submission button has been activated. "
                "Please ensure your map is ready to be verified."
            )

    async def _apply_vote_discord_side(self, *, thread_id: int, voter_id: int, difficulty_value: float) -> None:
        """Announce a cast vote and refresh the UI/plot.

        Args:
            code (str): Map code (for completeness/logging).
            thread_id (int): Playtest thread ID.
            voter_id (int): Discord user ID of the voter.
            difficulty_value (float): Raw difficulty value to convert and display.
        """
        label = convert_raw_difficulty_to_difficulty_all(difficulty_value)
        await self._rebuild_view_and_plot(thread_id=thread_id)
        await self._announce_vote_in_thread(thread_id=thread_id, voter_id=voter_id, label=label)

    async def _remove_vote_discord_side(self, *, thread_id: int, voter_id: int) -> None:
        """Announce a removed vote and refresh the UI/plot.

        Args:
            thread_id (int): Playtest thread ID.
            voter_id (int): Discord user ID of the voter.
        """
        await self._rebuild_view_and_plot(thread_id=thread_id)
        await self._announce_vote_in_thread(thread_id=thread_id, voter_id=voter_id, label=None)

    @register_queue_handler("api.playtest.vote.cast")
    async def _process_vote_cast(self, message: AbstractIncomingMessage) -> None:
        """Consume 'vote cast' events and apply thread-side updates.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestVoteCast`.
        """
        if message.headers.get("x-pytest-enabled", False):
            return

        s = msgspec.json.decode(message.body, type=PlaytestVoteCastMQ)
        await self._apply_vote_discord_side(
            thread_id=s.thread_id,
            voter_id=s.voter_id,
            difficulty_value=s.difficulty_value,
        )

    @register_queue_handler("api.playtest.vote.remove")
    async def _process_vote_remove(self, message: AbstractIncomingMessage) -> None:
        """Consume 'vote removed' events and apply thread-side updates.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestVoteRemoved`.
        """
        if message.headers.get("x-pytest-enabled", False):
            return
        s = msgspec.json.decode(message.body, type=PlaytestVoteRemovedMQ)
        await self._remove_vote_discord_side(
            thread_id=s.thread_id,
            voter_id=s.voter_id,
        )

    @register_queue_handler("api.playtest.approve")
    async def _process_approve_playtest(self, message: AbstractIncomingMessage) -> None:
        """Consume 'approve' playtest events and perform side effects.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestApprove`.
        """
        try:
            if message.headers.get("x-pytest-enabled", False):
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            s = msgspec.json.decode(message.body, type=PlaytestApproveMQ)

            await self._approve_playtest(
                code=s.code,
                thread_id=s.thread_id,
                verifier_id=s.verifier_id,
                difficulty=s.difficulty,
                primary_creator_id=s.primary_creator_id,
            )
        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    @register_queue_handler("api.playtest.force_accept")
    async def _process_force_accept_playtest(self, message: AbstractIncomingMessage) -> None:
        """Consume 'force accept' playtest events and perform side effects.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestForceAccept`.
        """
        try:
            if message.headers.get("x-pytest-enabled", False):
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            s = msgspec.json.decode(message.body, type=PlaytestForceAcceptMQ)
            log.debug(f"{s=}")
            playtest_data = await self.bot.api.get_playtest(s.thread_id)
            map_data = await self.bot.api.get_map(code=playtest_data.code)
            await self._force_accept_playtest(
                code=map_data.code,
                thread_id=s.thread_id,
                difficulty=s.difficulty,
                verifier_id=s.verifier_id,
                notify_primary_creator_id=map_data.primary_creator_id,
            )
        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    @register_queue_handler("api.playtest.force_deny")
    async def _process_force_deny_playtest(self, message: AbstractIncomingMessage) -> None:
        """Consume 'force deny' playtest events and perform side effects.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestForceDeny`.
        """
        try:
            if message.headers.get("x-pytest-enabled", False):
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            s = msgspec.json.decode(message.body, type=PlaytestForceDenyMQ)
            playtest_data = await self.bot.api.get_playtest(s.thread_id)
            map_data = await self.bot.api.get_map(code=playtest_data.code)
            await self._force_deny_playtest(
                code=map_data.code,
                thread_id=s.thread_id,
                verifier_id=s.verifier_id,
                reason=s.reason,
                notify_primary_creator_id=map_data.primary_creator_id,
            )
        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e

    @register_queue_handler("api.playtest.reset")
    async def _process_reset_playtest(self, message: AbstractIncomingMessage) -> None:
        """Consume 'reset' playtest events and perform side effects.

        Args:
            message (AbstractIncomingMessage): MQ message with `PlaytestReset`.
        """
        try:
            if message.headers.get("x-pytest-enabled", False):
                return

            assert message.message_id
            data = ClaimRequest(message.message_id)
            res = await self.bot.api.claim_idempotency(data)
            if not res.claimed:
                log.debug("[Idempotency] Duplicate: %s", message.message_id)
                return

            s = msgspec.json.decode(message.body, type=PlaytestResetMQ)
            playtest_data = await self.bot.api.get_playtest(s.thread_id)
            map_data = await self.bot.api.get_map(code=playtest_data.code)
            await self._reset_playtest_votes_and_completions(
                code=map_data.code,
                thread_id=s.thread_id,
                verifier_id=s.verifier_id,
                reason=s.reason,
                remove_votes=s.remove_votes,
                remove_completions=s.remove_completions,
                notify_primary_creator_id=map_data.primary_creator_id,
            )
        except Exception as e:
            await self.bot.api.delete_claimed_idempotency(data)
            raise e


class ModActionsDifficultyRatingSelect(discord.ui.Select):
    view: "ModActionsViewV2"

    def __init__(self) -> None:
        """Initialize the moderator difficulty select with all ranges."""
        options = [discord.SelectOption(value=x, label=x) for x in DIFFICULTY_RANGES_ALL]
        super().__init__(
            options=options,
            placeholder="What difficulty would you rate this map?",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Store the selected difficulty on the parent view and refresh.

        Args:
            itx (GenjiItx): The interaction that triggered the selection.
        """
        if self.values:
            self.view.difficulty = cast("DifficultyAll", self.values[0])
        self.view.rebuild()
        await itx.response.edit_message(view=self.view)


class ModActionsReasonModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter the reason for this action here...",
        style=discord.TextStyle.paragraph,
    )
    complete: bool | None

    def __init__(self, original_view: ModActionsViewV2) -> None:
        """Initialize the reason modal and bind the originating view.

        Args:
            original_view (ModActionsViewV2): The view to update on submit.
        """
        super().__init__(title="Reason")
        self.complete = None
        self.original_view = original_view

    async def on_submit(self, itx: GenjiItx) -> None:
        """Persist the reason back to the parent view and refresh it.

        Args:
            itx (GenjiItx): The modal submit interaction.
        """
        self.original_view.reason = self.reason.value
        self.original_view.rebuild()
        await itx.response.edit_message(view=self.original_view)
        self.stop()


class ModActionsReasonButton(discord.ui.Button):
    view: "ModActionsViewV2"

    def __init__(self) -> None:
        """Initialize the 'Enter Reason' button."""
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Enter Reason",
            row=0,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Open the reason modal.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        modal = ModActionsReasonModal(self.view)
        await itx.response.send_modal(modal)


class ModActionsConfirmButton(discord.ui.Button):
    view: "ModActionsViewV2"

    def __init__(self, disabled: bool) -> None:
        """Initialize the confirm button.

        Args:
            disabled (bool): Whether the button should start disabled.
        """
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Confirm",
            row=4,
            disabled=disabled,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Confirm the action, finalize the view, and stop waiting.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        self.view.confirmed = True
        self.view.clear_items()
        self.view.build_final_view("confirmed")
        await itx.response.edit_message(view=self.view)
        self.view.stop()


class ModActionsRejectButton(discord.ui.Button):
    view: "ModActionsViewV2"

    def __init__(self) -> None:
        """Initialize the reject button."""
        super().__init__(
            style=discord.ButtonStyle.red,
            label="Reject",
            row=4,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Reject the action, finalize the view, and stop waiting.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        self.view.clear_items()
        self.view.confirmed = False
        self.view.build_final_view("rejected")
        await itx.response.edit_message(view=self.view)
        self.view.stop()


class ModActionsViewV2(discord.ui.LayoutView):
    confirmed: bool | None = None
    difficulty: DifficultyAll | None = None
    reason: str | None = None

    def __init__(self, title: str, *, enable_reason: bool, enable_difficulty: bool) -> None:
        """Initialize the moderation action view.

        Args:
            title (str): Title shown in the view.
            enable_reason (bool): Whether to include the reason entry UI.
            enable_difficulty (bool): Whether to include the difficulty selector.
        """
        super().__init__()
        self.title = f"# {title}"
        self.enable_reason = enable_reason
        self.enable_difficulty = enable_difficulty
        self.rebuild()

    def rebuild(self) -> None:
        """Rebuild all UI components based on current state."""
        self.clear_items()
        container = discord.ui.Container(discord.ui.TextDisplay(self.title), discord.ui.Separator())
        if self.enable_reason:
            text = f"### Enter the reason for this action.\n\nCurrent reason:\n{self.reason}"
            reason_section = discord.ui.Section(
                discord.ui.TextDisplay(text),
                accessory=ModActionsReasonButton(),
            )

            container.add_item(reason_section)
            container.add_item(discord.ui.Separator())

        if self.enable_difficulty:
            text = f"### Enter the chosen difficulty for this map.\n\nCurrent difficulty selection:\n{self.difficulty}"
            difficulty_text = discord.ui.TextDisplay(text)
            difficulty_action_row = discord.ui.ActionRow(ModActionsDifficultyRatingSelect())

            container.add_item(difficulty_text)
            container.add_item(difficulty_action_row)
            container.add_item(discord.ui.Separator())

        confirmation_action_row = discord.ui.ActionRow(
            ModActionsConfirmButton(
                disabled=(self.enable_reason and not self.reason) or (self.enable_difficulty and not self.difficulty)
            ),
            ModActionsRejectButton(),
        )
        container.add_item(confirmation_action_row)
        self.add_item(container)

    def build_final_view(self, status: Literal["confirmed", "rejected"]) -> None:
        """Replace the view with a static confirmation/rejection message.

        Args:
            status (Literal["confirmed", "rejected"]): Final status to display.
        """
        self.clear_items()
        container = discord.ui.Container(discord.ui.TextDisplay(self.title), discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"This action has been {status}. You can dismiss this message."))
        self.add_item(container)


class DifficultyRatingSelect(discord.ui.Select["PlaytestComponentsV2View"]):
    """Select difficulty rating."""

    view: "PlaytestComponentsV2View"

    def __init__(self) -> None:
        """Initialize DifficultyRatingSelect."""
        options = [discord.SelectOption(value=x, label=x) for x in [*DIFFICULTY_RANGES_ALL, "Remove Vote"]]
        super().__init__(
            options=options,
            placeholder="What difficulty would you rate this map?",
            custom_id="playtest:difficulty",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Cast or remove a playtest vote via the API and acknowledge.

        Args:
            itx (GenjiItx): The interaction that triggered the select.
        """
        choice = self.values[0]
        await itx.response.defer(ephemeral=True, thinking=True)

        assert isinstance(itx.channel, discord.Thread)
        thread_id = itx.channel.id
        user_id = itx.user.id

        m = await itx.client.api.get_map(playtest_thread_id=thread_id)
        if user_id in (c.id for c in m.creators):
            raise UserFacingError("Vote failed. You cannot vote for your own map.")

        if choice == "Remove Vote":
            await itx.client.api.delete_playtest_vote(thread_id, user_id)
            return

        vote = PlaytestVote(
            difficulty=DIFFICULTY_MIDPOINTS[choice],  # type: ignore[arg-type]
        )
        await itx.client.api.cast_playtest_vote(thread_id, user_id, vote=vote)

        await itx.edit_original_response(content=f"You voted **{choice}**. Updating...")


class SelectOptionsTuple(NamedTuple):
    label: str
    description: str


_MOD_ONLY_OPTIONS_DATA = [
    SelectOptionsTuple("Force Accept", "Force submission through, overwriting difficulty votes."),
    SelectOptionsTuple("Force Deny", "Deny submission, deleting it and any associated completions/votes."),
    SelectOptionsTuple("Approve Submission", "Approve map submission, signing off on all difficulty votes."),
    SelectOptionsTuple(
        "Start Process Over", "Remove all completions and votes for a map without deleting the submission."
    ),
    SelectOptionsTuple("Remove Votes", "Remove all votes for a map without deleting the submission."),
    SelectOptionsTuple("Toggle Finalize Button", "Enable/Disable the Finalize button for the creator to use."),
]

_MOD_ONLY_OPTIONS = [
    discord.SelectOption(label=x.label, value=x.label, description=x.description) for x in _MOD_ONLY_OPTIONS_DATA
]

_CREATOR_ONLY_OPTIONS_DATA = [
    SelectOptionsTuple("Request Map Change", "Request a change such as code, category, or mechanics."),
    SelectOptionsTuple("Request Map Deletion", "Request to delete the map from the database."),
]

_CREATOR_ONLY_OPTIONS = [
    discord.SelectOption(label=x.label, value=x.label, description=x.description) for x in _CREATOR_ONLY_OPTIONS_DATA
]


class ModOnlySelectMenu(discord.ui.Select["PlaytestComponentsV2View"]):
    """Select mod commands."""

    view: PlaytestComponentsV2View

    def __init__(self) -> None:
        """Initialize the moderator-only options select."""
        super().__init__(
            options=_MOD_ONLY_OPTIONS,
            placeholder="Mod Only Options",
            custom_id="playtest:modonly",
        )

    async def callback(self, itx: GenjiItx) -> None:  # noqa: PLR0912, PLR0915
        """Handle moderator actions selected from the menu.

        Validates roles, prompts for any required inputs (reason/difficulty),
        invokes the appropriate playtest action, and refreshes the thread/UI.

        Args:
            itx (GenjiItx): The interaction that triggered the select.
        """
        assert itx.message
        await itx.message.edit(view=self.view)
        await self.view.fetch_data(itx.client)
        assert isinstance(itx.user, discord.Member)
        is_sensei = itx.user.get_role(itx.client.config.roles.admin.sensei)
        is_mod = itx.user.get_role(itx.client.config.roles.admin.mod)
        if not (is_mod or is_sensei):
            await itx.response.send_message("You are not a mod or a sensei!", ephemeral=True)
            return

        assert isinstance(itx.channel, discord.Thread)
        code = self.view.data.code
        thread_id = itx.channel.id
        primary_creator_id = self.view.data.primary_creator_id

        match self.values[0]:
            case "Force Accept":
                view = ModActionsViewV2("Force Accept", enable_difficulty=True, enable_reason=False)
                await itx.response.send_message(view=view, ephemeral=True)
                await view.wait()
                if not view.confirmed:
                    return
                assert view.difficulty

                payload = PlaytestForceAcceptCreate(difficulty=view.difficulty, verifier_id=itx.user.id)
                await itx.client.api.force_accept_playtest(thread_id, payload)

            case "Force Deny":
                view = ModActionsViewV2("Force Deny", enable_difficulty=False, enable_reason=True)
                await itx.response.send_message(view=view, ephemeral=True)
                await view.wait()
                if not view.confirmed:
                    return
                assert view.reason

                payload = PlaytestForceDenyCreate(verifier_id=itx.user.id, reason=view.reason)
                await itx.client.api.force_deny_playtest(thread_id, payload)

            case "Approve Submission":
                await itx.response.defer(ephemeral=True, thinking=True)
                if not self.view.data.playtest:
                    raise UserFacingError("This map data does not have a playtest attached. Contact nebula.")
                data = await itx.client.api.get_all_votes(self.view.data.playtest.thread_id)
                if not data.average:
                    raise UserFacingError("There are no votes for this playtest. Please use force accept instead.")
                if len(data.votes) < self.view.data.playtest_threshold:
                    message = (
                        "This playtest does not have enough votes to meet the threshold for approval. "
                        "Are you sure you want to approve it with the current vote average?"
                    )
                else:
                    message = "Are you sure you want to approve this submission?"
                confirmation_view = ConfirmationView(message)
                await itx.edit_original_response(view=confirmation_view)
                confirmation_view.original_interaction = itx
                await confirmation_view.wait()
                if not confirmation_view.confirmed:
                    return

                payload = PlaytestApproveCreate(itx.user.id)
                await itx.client.api.approve_playtest(thread_id, payload)

            case "Start Process Over":
                view = ModActionsViewV2("Start Process Over", enable_difficulty=False, enable_reason=True)
                await itx.response.send_message(view=view, ephemeral=True)
                await view.wait()
                if not view.confirmed:
                    return
                assert view.reason

                payload = PlaytestResetCreate(
                    verifier_id=itx.user.id,
                    reason=view.reason,
                    remove_votes=True,
                    remove_completions=True,
                )
                await itx.client.api.reset_playtest(thread_id, payload)

            case "Remove Votes":
                view = ModActionsViewV2("Remove Votes", enable_difficulty=False, enable_reason=True)
                await itx.response.send_message(view=view, ephemeral=True)
                await view.wait()
                if not view.confirmed:
                    return
                assert view.reason

                payload = PlaytestResetCreate(
                    verifier_id=itx.user.id,
                    reason=view.reason,
                    remove_votes=True,
                    remove_completions=False,
                )
                await itx.client.api.reset_playtest(thread_id, payload)

            case "Toggle Finalize Button":
                self.view.data.override_finalize = not self.view.data.override_finalize
                _view = self.view
                self.view.rebuild_components()
                await itx.response.edit_message(view=_view)

                state = "enabled" if _view.data.override_finalize else "disabled"
                _message = (
                    f"The Finalize button has been manually {state} for map "
                    f"({code}) by {itx.user.mention}.\n\n{_disabled_notifications_alert}"
                )
                delivered = await itx.client.notifications.notify_dm(
                    primary_creator_id, Notification.DM_ON_PLAYTEST_ALERTS, _message
                )
                if not delivered:
                    await itx.channel.send(_message)


class CreatorOnlySelectMenu(discord.ui.Select["PlaytestComponentsV2View"]):
    view: "PlaytestComponentsV2View"

    def __init__(self) -> None:
        """Initialize the creator-only options select."""
        super().__init__(
            options=_CREATOR_ONLY_OPTIONS,
            placeholder="Creator Only Options",
            custom_id="playtest:creatoronly",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Run creator-only actions for the current map/thread.

        Validates the caller is one of the map's creators and posts the
        appropriate request to the thread.

        Args:
            itx (GenjiItx): The interaction that triggered the select.
        """
        if itx.user.id not in (x.id for x in self.view.data.creators):
            await itx.response.send_message("You are not the creator of this map!", ephemeral=True)
            return

        match self.values[0]:
            case "Request Map Change":
                await itx.response.send_message(
                    f"<@&{itx.client.config.roles.mentionable.modmail}>, the creator is requesting a map change."
                    f"{itx.user.mention}, please be sure to mention what you need changed."
                )
            case "Request Map Deletion":
                await itx.response.send_message(
                    f"<@&{itx.client.config.roles.mentionable.modmail}>, "
                    f"{itx.user.mention} is requesting a map deletion."
                )

        assert itx.message
        await itx.message.edit(view=self.view)


class MapVerificationFlagWaitButton(discord.ui.Button):
    view: "MapFinalizationViewV2"

    def __init__(self, style: discord.ButtonStyle) -> None:
        """Initialize the toggle flag button.

        Args:
            style (discord.ButtonStyle): Initial button style (red/green).
        """
        super().__init__(
            custom_id="verification:mapflag:wait",
            style=style,
            label="Toggle Flag",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Toggle the flag color and refresh the view.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        if self.style == discord.ButtonStyle.red:
            self.view.rebuild_components(color=0xF04847, flag_style=discord.ButtonStyle.green)
        else:
            self.view.rebuild_components(color=0x40A258, flag_style=discord.ButtonStyle.red)
        await itx.response.edit_message(view=self.view)


class MapFinalizationViewV2(discord.ui.LayoutView):
    def __init__(self, guild_id: int, data: MapModel) -> None:
        """Initialize the map finalization view for verification.

        Args:
            guild_id (int): Guild ID to construct the thread URL.
            data (MapModel): Map data containing playtest meta.

        Raises:
            AttributeError: If the map data has no playtest meta.
        """
        super().__init__(timeout=None)
        self.data = data
        if not data.playtest:
            raise AttributeError("The data is missing playtest.")
        self.url = f"https://discord.com/channels/{guild_id}/{data.playtest.thread_id}"
        self.rebuild_components(flag_style=discord.ButtonStyle.red)

    def rebuild_components(
        self,
        *,
        color: discord.Color | int | None = None,
        flag_style: discord.ButtonStyle,
    ) -> None:
        """Rebuild the verification UI, optionally changing accent color.

        Args:
            color (discord.Color | int | None): Optional accent color.
            flag_style (discord.ButtonStyle): Style for the toggle flag button.
        """
        self.clear_items()
        assert self.data.map_banner
        container = discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(f"### {self.data.primary_creator_name} has marked a map as ready."),
                accessory=discord.ui.Button(style=discord.ButtonStyle.link, url=self.url, label="Go to playtest"),
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(FilteredFormatter(self.data).format()),
            discord.ui.MediaGallery(discord.MediaGalleryItem(media=self.data.map_banner)),
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "Something off about this submission? "
                    "Use this button to toggle the accent color to red indicating that there's an issue."
                ),
                accessory=MapVerificationFlagWaitButton(flag_style),
            ),
            accent_color=color,
        )
        self.add_item(container)

    async def on_error(self, itx: GenjiItx, error: Exception, item: Item[Any]) -> None:
        """On error handler."""
        await itx.client.tree.on_error(itx, cast("AppCommandError", error))


class FinalizeButton(discord.ui.Button["PlaytestComponentsV2View"]):
    view: "PlaytestComponentsV2View"

    def __init__(self, disabled: bool) -> None:
        """Initialize the finalize button.

        Args:
            disabled (bool): Whether finalization is currently disabled.
        """
        super().__init__(
            label="Finalize Playtest Submission",
            style=discord.ButtonStyle.green,
            disabled=disabled,
            custom_id="playtest:finalize",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Finalize the playtest submission and queue verification.

        Resets manual finalize override, updates the thread, posts to the
        verification queue with a view, and stores the verification message ID.

        Args:
            itx (GenjiItx): The interaction that triggered the button.

        Raises:
            AttributeError: If the map data has no playtest meta.
        """
        self.view.data.override_finalize = False
        self.view.rebuild_components()
        await itx.response.edit_message(view=self.view)
        assert isinstance(itx.channel, discord.Thread)
        await itx.channel.send("Map has been finalized. Please stand by for Sensei verification.")
        assert itx.guild
        verification_channel = itx.guild.get_channel(itx.client.config.channels.submission.verification_queue)
        assert isinstance(verification_channel, discord.TextChannel)
        view = MapFinalizationViewV2(itx.guild.id, self.view.data)
        verification_message = await verification_channel.send(view=view)

        playtest_edit_data = PlaytestPatchDTO(verification_id=verification_message.id)
        if not self.view.data.playtest:
            raise AttributeError("The data is missing playtest.")
        await itx.client.api.edit_playtest_meta(thread_id=self.view.data.playtest.thread_id, data=playtest_edit_data)


class PlaytestLayoutViewGallery(discord.ui.MediaGallery):
    def __init__(self, url: str) -> None:
        """Initialize a gallery with a single image URL.

        Args:
            url (str): Image URL to add to the gallery.
        """
        super().__init__()
        self.add_item(media=url)


class PlaytestComponentsV2View(discord.ui.LayoutView):
    def __init__(self, *, thread_id: int, data: MapModel) -> None:
        """Initialize the playtest components view for a thread.

        Args:
            thread_id (int): Playtest thread ID.
            data (MapModel): Current map data for rendering.
        """
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.data = data
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild all components for the playtest view (mods/creators/testers)."""
        self.clear_items()
        formatter = FilteredFormatter(self.data)
        assert self.data.map_banner
        data_section = discord.ui.Container(
            PlaytestLayoutViewGallery(self.data.map_banner),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content=formatter.format()),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content="## Mod Only Actions"),
            discord.ui.ActionRow(ModOnlySelectMenu()),
            discord.ui.TextDisplay(content="## Creator Only Actions"),
            discord.ui.ActionRow(CreatorOnlySelectMenu()),
            discord.ui.ActionRow(FinalizeButton(not self.data.finalizable)),
            discord.ui.Separator(),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://vote_hist.png"),
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content="## Playtester Actions"),
            discord.ui.ActionRow(DifficultyRatingSelect()),
        )

        self.add_item(data_section)

    async def fetch_data(self, bot: core.Genji) -> None:
        """Fetch fresh map data for this thread.

        Args:
            bot (core.Genji): Bot instance used to query the API.
        """
        overridden = self.data.override_finalize
        data = await bot.api.get_map(playtest_thread_id=self.thread_id)
        self.data = data
        self.data.override_finalize = overridden

    async def fetch_data_and_rebuild(self, bot: core.Genji) -> None:
        """Fetch fresh map data and rebuild the components.

        Args:
            bot (core.Genji): Bot instance used to query the API.
        """
        await self.fetch_data(bot)
        self.rebuild_components()

    async def on_error(self, itx: GenjiItx, error: Exception, item: Item[Any]) -> None:
        """On error handler."""
        await itx.client.tree.on_error(itx, cast("AppCommandError", error))


class PlaytestCog(BaseCog):
    playtest_views: ClassVar[dict[int, PlaytestComponentsV2View]] = {}
    verification_views: ClassVar[dict[int, MapFinalizationViewV2]] = {}
    _task: asyncio.Task

    async def cog_load(self) -> None:
        """Start post-RabbitMQ initialization when the cog loads."""
        self._task = asyncio.create_task(self.post_rabbit_load())

    async def post_rabbit_load(self) -> None:
        """Attach persistent views after MQ drains and data is available.

        Fetches in-progress playtests, reattaches verification/playtest views
        to their messages, and populates in-memory registries.

        Raises:
            AttributeError: If a fetched map lacks playtest meta.
        """
        await self.bot.rabbit.wait_until_drained()
        log.debug("Rabbit has been drained, moving on with PlaytestCog load.")
        _maps = await self.bot.api.get_maps(playtesting="In Progress")
        log.debug(f"{len(_maps)=}")
        for _map in _maps:
            log.debug(f"{_map.code=} is 'In Progress'")
            if not _map.playtest:
                log.warning(f"{_map.code=} playtest data not found. No thread association with this map.")
                continue
            if _map.playtest.verification_id and _map.playtest.verification_id not in self.verification_views:
                log.debug(f"{_map.code=} adding verification queue view now.")
                view = MapFinalizationViewV2(self.bot.config.guild, _map)
                self.bot.add_view(view, message_id=_map.playtest.verification_id)
                self.verification_views[_map.playtest.verification_id] = view

            if _map.playtest.thread_id not in self.playtest_views:
                log.debug(f"{_map.code=} adding playtest view now.")
                view = PlaytestComponentsV2View(thread_id=_map.playtest.thread_id, data=_map)
                self.bot.add_view(view, message_id=_map.playtest.thread_id)
                self.playtest_views[_map.playtest.thread_id] = view


async def setup(bot: core.Genji) -> None:
    """Load the playtest extension and register services.

    Args:
        bot (core.Genji): Bot instance to extend.
    """
    log.debug("[extensions.playtest] Setup called")
    bot.playtest = PlaytestService(bot)
    await bot.add_cog(PlaytestCog(bot))


async def teardown(bot: core.Genji) -> None:
    """Unload the playtest extension.

    Args:
        bot (core.Genji): Bot instance to modify.
    """
    await bot.remove_cog("PlaytestCog")
