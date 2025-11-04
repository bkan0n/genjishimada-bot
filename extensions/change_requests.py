from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import typing

import discord
from discord import ButtonStyle, ForumChannel, Guild, SelectOption, TextStyle, ui
from discord.app_commands import Transform, command
from discord.ext import tasks
from genjipk_sdk.models import ChangeRequestCreateDTO, ChangeRequestType
from genjipk_sdk.utilities._types import OverwatchCode

from utilities.base import BaseCog, BaseView
from utilities.change_requests import FormattableChangeRequest, FormattableStaleChangeRequest
from utilities.errors import UserFacingError
from utilities.formatter import FilteredFormatter
from utilities.maps import MapModel
from utilities.paginator import PaginatorView
from utilities.transformers import CodeAllTransformer

if typing.TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx


log = logging.getLogger(__name__)


class ChangeRequestModal(ui.Modal):
    def __init__(self) -> None:
        """Initialize the Change Request modal.

        Sets the modal title, timeout, and initializes the `submitted` flag.
        """
        super().__init__(
            title="Change Request",
            timeout=600,
        )
        self.submitted = False

    change_request_type = ui.Label(
        text="Change Request Type",
        description="The type of Change Request.",
        component=ui.Select(
            options=[
                SelectOption(
                    label="Difficulty Change",
                    value="Difficulty Change",
                    description="The map's difficulty is incorrect or unbalanced.",
                ),
                SelectOption(
                    label="Map Geometry",
                    value="Map Geometry",
                    description="A problem with the physical layout or geometry of the map.",
                ),
                SelectOption(
                    label="Map Edit Required",
                    value="Map Edit Required",
                    description="The map needs specific edits or adjustments.",
                ),
                SelectOption(
                    label="Framework/Workshop",
                    value="Framework/Workshop",
                    description="A bug caused by the framework or Workshop.",
                ),
                SelectOption(
                    label="Other", value="Other", description="Any issue that doesn't fit the categories above."
                ),
            ],
        ),
    )

    feedback = ui.Label(
        text="What change are you requesting?",
        description="Type your feedback here and please be specific. If you have images, attach them in the thread.",
        component=ui.TextInput(
            style=TextStyle.long,
            max_length=256,
        ),
    )

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle modal submission.

        Sends an ephemeral confirmation, marks the modal as submitted, and
        stops the modal to unblock the caller.

        Args:
            itx (GenjiItx): The interaction associated with this modal submission.
        """
        await itx.response.send_message("Details have been edited.", ephemeral=True)
        self.submitted = True
        self.stop()


class ChangeRequestThreadJumpButton(ui.Button):
    def __init__(self, url: str) -> None:
        """Create a thread jump button.

        Args:
            url (str): The absolute URL of the target thread.
        """
        super().__init__(style=ButtonStyle.link, label="Go to thread", url=url)


class DuplicateChangeRequestsView(PaginatorView[FormattableChangeRequest | FormattableStaleChangeRequest]):
    guild: Guild
    change_request_channel: ForumChannel

    def __init__(
        self,
        bot: Genji,
        code: str,
        change_requests: list[FormattableChangeRequest] | list[FormattableStaleChangeRequest],
    ) -> None:
        """Construct the duplicate change requests view.

        Args:
            bot (Genji): The running bot instance.
            code (str): The Overwatch map code the user is targeting.
            change_requests (list[FormattableChangeRequest] | list[FormattableStaleChangeRequest]):
                Change requests to display in the paginator.
        """
        self.bot = bot
        guild = bot.get_guild(bot.config.guild)
        assert guild
        self.guild = guild

        channel = guild.get_channel(bot.config.channels.help.change_requests)
        assert isinstance(channel, ForumChannel)
        self.change_request_channel = channel

        super().__init__(title=f"Open Change Requests for {code}", data=change_requests)
        self.value = False

    def build_additional_action_row(self) -> ui.ActionRow:
        """Build the action row containing continue/cancel buttons.

        Returns:
            ui.ActionRow: Action row populated with continue and cancel buttons.
        """
        return ui.ActionRow(DuplicateChangeRequestContinueButton(), DuplicateChangeRequestCancelButton())

    def build_page_body(self) -> list[ui.Section]:
        """Create the body sections for the current paginator page.

        Formats each change request into a section with a thread jump button and
        labels items as resolved or unresolved.

        Returns:
            list[ui.Section]: The UI sections representing the current page.
        """
        sections = []
        for i, cr in enumerate(self.current_page, start=self.item_index_offset + 1):
            label = "Resolved" if cr.resolved else "Unresolved"
            name = f"{label} Change Request {i}"
            content = FilteredFormatter(cr).format()
            url = f"https://discord.com/channels/{self.guild.id}/{cr.thread_id}"
            button = ChangeRequestThreadJumpButton(url)
            section = ui.Section(ui.TextDisplay(f"### {name}\n{content}"), accessory=button)
            sections.append(section)
        return sections


class DuplicateChangeRequestContinueButton(ui.Button[DuplicateChangeRequestsView]):
    view: DuplicateChangeRequestsView

    def __init__(self) -> None:
        """Initialize the continue button."""
        super().__init__(label="Continue making change request", style=ButtonStyle.green)

    async def callback(self, itx: GenjiItx) -> None:
        """Handle click to continue the flow.

        Sets the parent view's `value` to True, stops the view, and notifies the user.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        self.view.value = True
        self.view.stop()
        await itx.response.send_message("Please continue with your change request.", ephemeral=True)


class DuplicateChangeRequestCancelButton(ui.Button[DuplicateChangeRequestsView]):
    view: DuplicateChangeRequestsView | ChangeRequestConfirmationView

    def __init__(self) -> None:
        """Initialize the cancel button."""
        super().__init__(label="Cancel", style=ButtonStyle.red)

    async def callback(self, itx: GenjiItx) -> None:
        """Handle click to cancel the flow.

        Stops the parent view, sends an ephemeral confirmation, and attempts to
        delete the original response (suppressed on missing permissions or if the
        message no longer exists).

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        self.view.stop()
        await itx.response.send_message("Change request cancelled.", ephemeral=True)
        if self.view.original_interaction:
            with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self.view.original_interaction.delete_original_response()


class ChangeRequestModCloseView(ui.View):
    def __init__(self) -> None:
        """Initialize the moderator close view."""
        super().__init__(timeout=None)

    async def interaction_check(self, itx: GenjiItx) -> bool:
        """Verify the user has permission to use this view.

        Allows only members with configured moderator roles to proceed.

        Args:
            itx (GenjiItx): The interaction to validate.

        Raises:
            UserFacingError: If the user lacks the required roles.
        """
        assert itx.guild and isinstance(itx.user, discord.Member)
        sensei = itx.guild.get_role(itx.client.config.roles.admin.sensei)
        moderator = itx.guild.get_role(itx.client.config.roles.admin.mod)
        permitted = sensei in itx.user.roles or moderator in itx.user.roles
        if not permitted:
            raise UserFacingError("You are not allowed to use this.")
        return True

    @ui.button(
        label="Close (Sensei Only)",
        style=ButtonStyle.red,
        custom_id="CR-ModClose",
        row=1,
        emoji="\N{HEAVY MULTIPLICATION X}",
    )
    async def callback(self, itx: GenjiItx, button: ui.Button) -> None:
        """Close the current thread and mark it resolved.

        Adds the 'Resolved' forum tag if available, archives and locks the thread,
        and notifies the API that the change request was resolved.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
            button (ui.Button): The invoking button instance.
        """
        await itx.response.send_message("Closing thread.")

        thread = itx.channel
        assert isinstance(thread, discord.Thread) and itx.guild
        forum = itx.guild.get_channel(itx.client.config.channels.help.change_requests)
        assert isinstance(forum, discord.ForumChannel)

        resolved_tag = next((t for t in forum.available_tags if t.name == "Resolved"), None)
        tags = list(thread.applied_tags)
        if resolved_tag and resolved_tag not in tags:
            tags.append(resolved_tag)

        await thread.edit(archived=True, locked=True, applied_tags=tags[:5])
        await itx.client.api.resolve_change_request(thread.id)


async def _change_request_interaction_check(itx: GenjiItx, thread_id: int, code: OverwatchCode) -> bool:
    """Check whether a user is allowed to act on a change request.

    Users are permitted if the API grants permission for the given thread/code,
    or if they possess moderator roles. Sends an error message to the original
    response if permission is denied.

    Args:
        itx (GenjiItx): The interaction context.
        thread_id (int): The target change request thread ID.
        code (OverwatchCode): The associated Overwatch map code.

    Returns:
        bool: True if the user is permitted; False otherwise.
    """
    user_permitted = await itx.client.api.check_permission_for_change_request(
        thread_id,
        itx.user.id,
        code,
    )

    check = user_permitted
    if not check:
        assert itx.guild and isinstance(itx.user, discord.Member)
        sensei = itx.guild.get_role(itx.client.config.roles.admin.sensei)
        moderator = itx.guild.get_role(itx.client.config.roles.admin.mod)
        check = sensei in itx.user.roles or moderator in itx.user.roles

    if not check:
        await itx.edit_original_response(content="You do not have permission to use this.")
    return check


class ChangeRequestArchiveMapButton(
    ui.DynamicItem[ui.Button],
    template=r"FCRA-(?P<code>[A-Z0-9]{4,6})-(?P<thread_id>\d+)",
):
    def __init__(self, code: str, thread_id: int) -> None:
        """Initialize the archive request button.

        Args:
            code (str): Map code associated with the change request.
            thread_id (int): The change request thread ID.
        """
        custom_id = "-".join(["FCRA", code, str(thread_id)])
        super().__init__(
            ui.Button(
                label="Request Map Archive",
                style=ButtonStyle.red,
                custom_id=custom_id,
                emoji="\N{CARD FILE BOX}",
            )
        )
        self.thread_id = thread_id
        self.code = code

    async def interaction_check(self, itx: GenjiItx) -> bool:
        """Gate this button behind the shared change request permission rules.

        Args:
            itx (GenjiItx): The interaction to validate.

        Returns:
            bool: True if permitted; False otherwise.
        """
        return await _change_request_interaction_check(itx, self.thread_id, self.code)

    @classmethod
    async def from_custom_id(
        cls, itx: GenjiItx, item: ui.Button, match: re.Match[str]
    ) -> ChangeRequestArchiveMapButton:
        """Reconstruct an instance from a matching custom_id.

        Args:
            itx (GenjiItx): The interaction context.
            item (ui.Button): The raw button instance.
            match (re.Match[str]): Regex match containing 'code' and 'thread_id'.

        Returns:
            ChangeRequestArchiveMapButton: A configured dynamic button instance.
        """
        return cls(match["code"], int(match["thread_id"]))

    async def callback(self, itx: GenjiItx) -> None:
        """Notify moderators that a map archive is being requested.

        Edits the original response for feedback and posts a message in the
        current thread tagging the moderator group.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        await itx.response.send_message(content="Requesting map archive.")
        assert isinstance(itx.channel, discord.Thread)
        await itx.channel.send(
            f"<@&{itx.client.config.roles.mentionable.modmail}>\n\n{itx.user.mention} is requesting map archive.",
            view=ChangeRequestModCloseView(),
        )


class ChangeRequestConfirmChangesButton(
    ui.DynamicItem[ui.Button],
    template=r"FCRC-(?P<code>[A-Z0-9]{4,6})-(?P<thread_id>\d+)",
):
    def __init__(self, code: str, thread_id: int) -> None:
        """Initialize the confirm changes button.

        Args:
            code (str): Map code associated with the change request.
            thread_id (int): The change request thread ID.
        """
        custom_id = "-".join(["FCRC", code, str(thread_id)])
        super().__init__(
            ui.Button(
                label="Confirm changes have been made",
                style=ButtonStyle.green,
                custom_id=custom_id,
                emoji="\N{THUMBS UP SIGN}",
            )
        )
        self.thread_id = thread_id
        self.code = code

    async def interaction_check(self, itx: GenjiItx) -> bool:
        """Gate this button behind the shared change request permission rules.

        Args:
            itx (GenjiItx): The interaction to validate.

        Returns:
            bool: True if permitted; False otherwise.
        """
        return await _change_request_interaction_check(itx, self.thread_id, self.code)

    @classmethod
    async def from_custom_id(
        cls, itx: GenjiItx, item: ui.Button, match: re.Match[str]
    ) -> ChangeRequestConfirmChangesButton:
        """Reconstruct an instance from a matching custom_id.

        Args:
            itx (GenjiItx): The interaction context.
            item (ui.Button): The raw button instance.
            match (re.Match[str]): Regex match containing 'code' and 'thread_id'.

        Returns:
            ChangeRequestConfirmChangesButton: A configured dynamic button instance.
        """
        return cls(match["code"], int(match["thread_id"]))

    async def callback(self, itx: GenjiItx) -> None:
        """Post a confirmation message that changes have been applied.

        Edits the original response for feedback and posts to the thread with a
        moderator-only view.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        await itx.response.send_message(content="Confirming changes have been made.")
        assert isinstance(itx.channel, discord.Thread)
        await itx.channel.send(
            f"<@&{itx.client.config.roles.mentionable.modmail}>\n\n{itx.user.mention} "
            "has confirmed changes have been made.",
            view=ChangeRequestModCloseView(),
        )


class ChangeRequestDenyChangesButton(
    ui.DynamicItem[ui.Button],
    template=r"FCRD-(?P<code>[A-Z0-9]{4,6})-(?P<thread_id>\d+)",
):
    def __init__(self, code: str, thread_id: int) -> None:
        """Initialize the deny changes button.

        Args:
            code (str): Map code associated with the change request.
            thread_id (int): The change request thread ID.
        """
        custom_id = "-".join(["FCRD", code, str(thread_id)])
        super().__init__(
            ui.Button(
                label="Deny changes as non applicable",
                style=ButtonStyle.red,
                custom_id=custom_id,
                emoji="\N{HEAVY MULTIPLICATION X}",
            )
        )
        self.thread_id = thread_id
        self.code = code

    async def interaction_check(self, itx: GenjiItx) -> bool:
        """Gate this button behind the shared change request permission rules.

        Args:
            itx (GenjiItx): The interaction to validate.

        Returns:
            bool: True if permitted; False otherwise.
        """
        return await _change_request_interaction_check(itx, self.thread_id, self.code)

    @classmethod
    async def from_custom_id(
        cls, itx: GenjiItx, item: ui.Button, match: re.Match[str]
    ) -> ChangeRequestDenyChangesButton:
        """Reconstruct an instance from a matching custom_id.

        Args:
            itx (GenjiItx): The interaction context.
            item (ui.Button): The raw button instance.
            match (re.Match[str]): Regex match containing 'code' and 'thread_id'.

        Returns:
            ChangeRequestDenyChangesButton: A configured dynamic button instance.
        """
        return cls(match["code"], int(match["thread_id"]))

    async def callback(self, itx: GenjiItx) -> None:
        """Post a message indicating the request is denied as non-applicable.

        Edits the original response for feedback and posts to the thread with a
        moderator-only view.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        await itx.response.send_message(content="Denying changes.")
        assert isinstance(itx.channel, discord.Thread)
        await itx.channel.send(
            f"<@&{itx.client.config.roles.mentionable.modmail}>\n\n"
            f"{itx.user.mention} is denying changes as non applicable.",
            view=ChangeRequestModCloseView(),
        )


class ChangeRequestView(ui.View):
    def __init__(self, code: str, thread_id: int) -> None:
        """Initialize the change request view for a specific thread.

        Args:
            code (str): Map code associated with the change request.
            thread_id (int): The change request thread ID.
        """
        super().__init__(timeout=None)
        self.add_item(ChangeRequestConfirmChangesButton(code, thread_id))
        self.add_item(ChangeRequestDenyChangesButton(code, thread_id))
        self.add_item(ChangeRequestArchiveMapButton(code, thread_id))


class ChangeRequestEditDetailsButton(ui.Button["ChangeRequestConfirmationView"]):
    view: ChangeRequestConfirmationView

    def __init__(self) -> None:
        """Initialize the edit details button."""
        super().__init__(label="Edit Details", style=ButtonStyle.blurple, row=0)

    async def callback(self, itx: GenjiItx) -> None:
        """Open the details modal and refresh the confirmation view.

        Displays the modal, waits for user submission, then enables the Submit
        button and refreshes the view if the modal was submitted.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        await itx.response.send_modal(self.view.edit_details_modal)
        await self.view.edit_details_modal.wait()
        if not self.view.edit_details_modal.submitted:
            return

        self.view.submit_button.disabled = False
        self.view.rebuild_components()
        await itx.edit_original_response(view=self.view)


class ChangeRequestSubmitButton(ui.Button["ChangeRequestConfirmationView"]):
    view: ChangeRequestConfirmationView

    def __init__(self) -> None:
        """Initialize the submit button, disabled until details are present."""
        super().__init__(label="Submit", style=ButtonStyle.green, row=0, disabled=True)

    async def callback(self, itx: GenjiItx) -> None:
        """Create a change request thread and persist the request.

        Builds the thread content (including creator mentions), creates a forum
        thread with appropriate tags, persists the change request via the API,
        attaches the moderation view, and cleans up the original interaction.

        Args:
            itx (GenjiItx): The interaction that triggered the button.
        """
        await itx.response.defer(ephemeral=True)
        self.view.stop()

        assert itx.guild
        channel = itx.guild.get_channel(itx.client.config.channels.help.change_requests)
        assert isinstance(channel, discord.ForumChannel)

        assert isinstance(self.view.edit_details_modal.feedback.component, ui.TextInput)
        assert isinstance(self.view.edit_details_modal.change_request_type.component, ui.Select)

        members = [itx.guild.get_member(creator.id) for creator in self.view.map_data.creators]
        if not any(members):
            mentions = (
                f"<@&{itx.client.config.roles.mentionable.modmail}>\n-# The creator of this map is not in this server."
            )
        else:
            mentions = "".join(f"{member.mention}" for member in members if member)
        content = (
            f"# {mentions}\n\n"
            f"## {itx.user.mention} is requesting changes for map **{self.view.code}**\n\n"
            f"{self.view.edit_details_modal.feedback.component.value}"
        )

        thread = await channel.create_thread(
            name=f"CR-{self.view.code} Discussion",
            content=content,
            applied_tags=self._construct_forum_tags(channel),
        )
        change_request = ChangeRequestCreateDTO(
            thread_id=thread[0].id,
            user_id=itx.user.id,
            code=self.view.code,
            content=self.view.edit_details_modal.feedback.component.value,
            change_request_type=typing.cast(
                "ChangeRequestType", self.view.edit_details_modal.change_request_type.component.values[0]
            ),
            creator_mentions=mentions,
        )
        await itx.client.api.create_change_request(change_request)
        view = ChangeRequestView(self.view.code, thread[0].id)
        await thread[1].edit(view=view)
        await itx.delete_original_response()
        await thread[0].send(view=ChangeRequestModCloseView())

    def _construct_forum_tags(self, channel: discord.ForumChannel) -> list[discord.ForumTag]:
        """Map the selected change request type to forum tags.

        Args:
            channel (discord.ForumChannel): The forum channel where the thread will be created.

        Returns:
            list[discord.ForumTag]: Matching tags to apply to the new thread.
        """
        assert isinstance(channel, discord.ForumChannel)
        assert isinstance(self.view.edit_details_modal.change_request_type.component, ui.Select)
        type_ = self.view.edit_details_modal.change_request_type.component.values[0]
        return [tag for tag in channel.available_tags if tag.name == type_]


class ChangeRequestConfirmationView(BaseView):
    edit_details_modal: ChangeRequestModal
    edit_details_button: ChangeRequestEditDetailsButton
    submit_button: ChangeRequestSubmitButton
    cancel_button: DuplicateChangeRequestCancelButton

    def __init__(self, code: str, map_data: MapModel) -> None:
        """Initialize the confirmation view with map data and UI controls.

        Args:
            code (str): Map code for the change request.
            map_data (MapModel): Map metadata used for the summary display.
        """
        self.code = code
        self.map_data = map_data
        self.edit_details_modal = ChangeRequestModal()
        self.edit_details_button = ChangeRequestEditDetailsButton()
        self.submit_button = ChangeRequestSubmitButton()
        self.cancel_button = DuplicateChangeRequestCancelButton()
        super().__init__()

    def rebuild_components(self) -> None:
        """Rebuild the view components based on the latest modal state.

        Renders the map summary, change request summary (if available), and the
        action rows (Edit, Submit, Cancel).
        """
        self.clear_items()
        map_string = FilteredFormatter(self.map_data).format()

        assert isinstance(self.edit_details_modal.feedback.component, ui.TextInput)
        assert isinstance(self.edit_details_modal.change_request_type.component, ui.Select)

        cr_types = self.edit_details_modal.change_request_type.component.values
        feedback = self.edit_details_modal.feedback.component.value
        display = ()
        if cr_types and feedback:
            display = (ui.TextDisplay(f"# Change Request:\n> `Type` {cr_types[0]}\n> `Request` {feedback}"),)

        container = ui.Container(
            ui.TextDisplay(
                f"# Add the details to your change request and confirm everything looks correct.\n{map_string}"
            ),
            *display,
            ui.Separator(),
            ui.TextDisplay(self._end_time_string),
            ui.ActionRow(self.edit_details_button),
            ui.ActionRow(self.submit_button, self.cancel_button),
        )
        self.add_item(container)


class ChangeRequestsCog(BaseCog):
    _task: asyncio.Task

    async def _set_up_views(self) -> None:
        """Start tasks/views after the bot is ready."""
        await self.bot.wait_until_ready()
        self.alert_stale_change_requests.start()

    async def cog_load(self) -> None:
        """Start background setup when the cog is loaded."""
        self._task = asyncio.create_task(self._set_up_views())

    async def cog_unload(self) -> None:
        """Stop background tasks when the cog is unloaded."""
        self.alert_stale_change_requests.stop()

    @command(name="change-request")
    async def change_request(
        self,
        itx: GenjiItx,
        code: Transform[str, CodeAllTransformer],
    ) -> None:
        """Create a change request for a given map code.

        Checks for existing open/stale requests and, if found, shows a paginator
        to review them. If the user decides to continue (or none exist), shows a
        confirmation view to collect and submit request details.

        Args:
            itx (GenjiItx): The command interaction.
            code (Transform[str, CodeAllTransformer]): The target map code.
        """
        await itx.response.defer(ephemeral=True)
        change_requests = await self.bot.api.get_change_requests(code)
        if change_requests:
            view = DuplicateChangeRequestsView(self.bot, code, change_requests)
            await itx.edit_original_response(view=view)
            view.original_interaction = itx
            await view.wait()
            if not view.value:
                return

        assert itx.guild
        forum = itx.guild.get_channel(self.bot.config.channels.help.change_requests)
        assert isinstance(forum, discord.ForumChannel)

        map_data = await self.bot.api.get_map(code=code)
        view = ChangeRequestConfirmationView(code, map_data)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @tasks.loop(hours=1)
    async def alert_stale_change_requests(self) -> None:
        """Periodically alert stale change requests in their threads.

        Fetches stale requests from the API, locates their threads, posts a
        reminder with a moderation view, and marks each as alerted to prevent
        duplicate notifications.
        """
        rows = await self.bot.api.get_stale_change_requests()
        for row in rows:
            thread = self.bot.get_channel(row.thread_id)
            if not thread:
                thread = self.bot.fetch_channel(row.thread_id)
            if not thread:
                log.warning("Stale CR alert: id=%s exists in db but no Thread exists. Skipping.", row.thread_id)
                continue
            assert isinstance(thread, discord.Thread)
            await thread.send(
                f"{row.creator_mentions}<@&{self.bot.config.roles.mentionable.modmail}>\n"
                "# This change request is now stale. "
                "If you have made the necessary changes, please click the button above to confirm.",
                view=ChangeRequestModCloseView(),
            )
            await self.bot.api.update_alerted_change_request(row.thread_id)


async def setup(bot: Genji) -> None:
    """Add the ChangeRequestsCog and related dynamic items to the bot.

    Args:
        bot (Genji): The bot instance to register with.
    """
    await bot.add_cog(ChangeRequestsCog(bot))
    bot.add_dynamic_items(ChangeRequestConfirmChangesButton)
    bot.add_dynamic_items(ChangeRequestDenyChangesButton)
    bot.add_dynamic_items(ChangeRequestArchiveMapButton)
    bot.add_view(ChangeRequestModCloseView())
