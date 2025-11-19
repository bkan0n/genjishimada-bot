from __future__ import annotations

import typing
from logging import getLogger

from discord import ButtonStyle, TextStyle, app_commands, ui
from genjipk_sdk.users import (
    NOTIFICATION_TYPES,
    Notification,
    OverwatchUsernameItem,
    OverwatchUsernamesResponse,
    OverwatchUsernamesUpdateRequest,
)

from utilities.base import BaseCog, BaseView

if typing.TYPE_CHECKING:
    from core.genji import Genji
    from utilities._types import GenjiItx

log = getLogger(__name__)


def bool_string(value: bool) -> str:
    """Return ON or OFF depending on the boolean value given."""
    if value:
        return "ON"
    else:
        return "OFF"


ENABLED_EMOJI = "🔔"
DISABLED_EMOJI = "🔕"


class SettingsView(BaseView):
    def __init__(self, flags: Notification, current_usernames: OverwatchUsernamesResponse) -> None:
        """Initialize SettingsView.

        Args:
            flags (Notification): The flags currently assigned to the user.
            current_usernames (OverwatchUsernamesResponse): The user names a user currently has assigned.
        """
        self.flags = flags
        self.current_usernames = current_usernames
        super().__init__(timeout=360)
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the necessary components for the view."""
        self.clear_items()

        self._dm_on_verfication_button = NotificationButton("DM_ON_VERIFICATION", self.flags)
        self._dm_on_skill_role_update_button = NotificationButton("DM_ON_SKILL_ROLE_UPDATE", self.flags)
        self._dm_on_lootbox_gain_button = NotificationButton("DM_ON_LOOTBOX_GAIN", self.flags)
        self._dm_on_records_removal_button = NotificationButton("DM_ON_RECORDS_REMOVAL", self.flags)
        self._dm_on_playtest_alerts_button = NotificationButton("DM_ON_PLAYTEST_ALERTS", self.flags)
        self._ping_on_xp_gain_button = NotificationButton("PING_ON_XP_GAIN", self.flags)
        self._ping_on_mastery_button = NotificationButton("PING_ON_MASTERY", self.flags)
        self._ping_on_community_rank_update_button = NotificationButton("PING_ON_COMMUNITY_RANK_UPDATE", self.flags)

        container = ui.Container(
            ui.TextDisplay("# Settings"),
            ui.Separator(),
            ui.TextDisplay("### Direct Messages"),
            ui.Section(
                ui.TextDisplay("Direct message on completion/records verification."),
                accessory=self._dm_on_verfication_button,
            ),
            ui.Section(
                ui.TextDisplay("Direct message on skill role updates."),
                accessory=self._dm_on_skill_role_update_button,
            ),
            ui.Section(
                ui.TextDisplay("Direct message on lootbox gain."),
                accessory=self._dm_on_lootbox_gain_button,
            ),
            ui.Section(
                ui.TextDisplay("Direct message on record/completion removal."),
                accessory=self._dm_on_records_removal_button,
            ),
            ui.Section(
                ui.TextDisplay("Direct message on followed playtest updates."),
                accessory=self._dm_on_playtest_alerts_button,
            ),
            ui.TextDisplay("### Pings"),
            ui.Section(
                ui.TextDisplay("Ping in XP channel when XP gained."),
                accessory=self._ping_on_xp_gain_button,
            ),
            ui.Section(
                ui.TextDisplay("Ping in XP channel when map mastery gained."),
                accessory=self._ping_on_mastery_button,
            ),
            ui.Section(
                ui.TextDisplay("Ping in XP channel when community rank has changed."),
                accessory=self._ping_on_community_rank_update_button,
            ),
            ui.Separator(),
            ui.TextDisplay("# Overwatch Usernames"),
            ui.Section(
                ui.TextDisplay(
                    "Set your Overwatch username and alt accounts (if any). "
                    "This helps speed up the verification process"
                ),
                accessory=OpenOverwatchUsernamesModalButton(self.current_usernames),
            ),
            ui.Separator(),
            ui.TextDisplay(self._end_time_string),
        )
        self.add_item(container)


class NotificationButton(ui.Button["SettingsView"]):
    view: SettingsView

    def __init__(self, notification_type: NOTIFICATION_TYPES, flags: Notification) -> None:
        """Initialize NotificationButton.

        Args:
            notification_type (NOTIFICATION_TYPES): The type of notification.
            flags (Notification): The flags currently assigned to the user.
        """
        super().__init__()
        self.notification_type: NOTIFICATION_TYPES = notification_type
        self.value = getattr(Notification, notification_type, Notification.NONE)
        enabled = self.value in flags
        self._edit_button(enabled)

    async def callback(self, itx: GenjiItx) -> None:
        """Notification button callback."""
        self.view.flags ^= self.value
        enabled = self.value in self.view.flags
        self._edit_button(enabled)
        await itx.response.edit_message(view=self.view)
        await itx.client.api.update_notification(itx.user.id, self.notification_type, enabled)

    def _edit_button(self, enabled: bool) -> None:
        """Edit button."""
        self.label = bool_string(enabled)
        self.emoji = ENABLED_EMOJI if enabled else DISABLED_EMOJI
        self.style = ButtonStyle.green if enabled else ButtonStyle.red


class OpenOverwatchUsernamesModalButton(ui.Button["SettingsView"]):
    view: "SettingsView"

    def __init__(self, current_usernames: OverwatchUsernamesResponse) -> None:
        """Initialize OpenOverwatchUsernamesModalButton.

        Args:
            current_usernames (OverwatchUsernamesResponse): The user names a user currently has assigned.
        """
        self.current_usernames = current_usernames
        super().__init__(style=ButtonStyle.green, label="Edit")

    async def callback(self, itx: GenjiItx) -> None:
        """Add Overwatch username button callback."""
        modal = OverwatchUsernameModal(self.current_usernames)
        await itx.response.send_modal(modal)
        await modal.wait()
        if not modal.completed:
            return

        inputs = (modal.primary, modal.secondary, modal.tertiary)
        new_usernames = []
        for i in inputs:
            assert isinstance(i.component, ui.TextInput)
            if i.component.value:
                new_usernames.append(OverwatchUsernameItem(i.component.value, i.text == "Primary Overwatch Username"))
        await itx.client.api.update_overwatch_usernames(itx.user.id, OverwatchUsernamesUpdateRequest(new_usernames))
        self.view.current_usernames = await itx.client.api.get_overwatch_usernames(itx.user.id)
        _view = self.view
        self.view.rebuild_components()
        await itx.edit_original_response(view=_view)


class OverwatchUsernameModal(ui.Modal):
    def __init__(self, current_usernames: OverwatchUsernamesResponse) -> None:
        """Initialize OverwatchUsernameModal.

        Args:
            current_usernames (OverwatchUsernamesResponse): The user names a user currently has assigned.
        """
        self.completed = False
        self.current_usernames = current_usernames
        super().__init__(title="Set Overwatch Usernames")
        self.build_components()

    def build_components(self) -> None:
        """Build the necessary components."""
        self.primary = ui.Label(
            text="Primary Overwatch Username",
            component=ui.TextInput(
                style=TextStyle.short,
                placeholder=("Enter your primary Overwatch username. The number after your username is not required."),
                default=self.current_usernames.primary,
                max_length=25,
                required=True,
            ),
        )

        self.secondary = ui.Label(
            text="Alt Overwatch Username 1",
            component=ui.TextInput(
                style=TextStyle.short,
                placeholder=("Enter an alternate Overwatch username. The number after your username is not required."),
                default=self.current_usernames.secondary,
                max_length=25,
                required=False,
            ),
        )

        self.tertiary = ui.Label(
            text="Alt Overwatch Username 2",
            component=ui.TextInput(
                style=TextStyle.short,
                placeholder=("Enter an alternate Overwatch username. The number after your username is not required."),
                default=self.current_usernames.tertiary,
                max_length=25,
                required=False,
            ),
        )
        self.add_item(self.primary)
        self.add_item(self.secondary)
        self.add_item(self.tertiary)

    async def on_submit(self, itx: GenjiItx) -> None:
        """Callback for the modal."""
        self.completed = True
        await itx.response.send_message("Overwatch names have been set.", ephemeral=True)


class SettingsCog(BaseCog):
    @app_commands.command()
    async def settings(self, itx: GenjiItx) -> None:
        """Change various settings like notifications and your display name."""
        await itx.response.defer(ephemeral=True)
        flags = await self.bot.api.get_notification_flags(itx.user.id)
        current_usernames = await self.bot.api.get_overwatch_usernames(itx.user.id)
        view = SettingsView(flags, current_usernames)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @app_commands.command(name="rank-card")
    async def rank_card(self, itx: GenjiItx) -> None:
        """View the rank card of a user."""
        await itx.response.send_message(
            "This feature has been permanently moved to our website.\nhttps://genji.pk/rank_card",
            ephemeral=True,
        )


async def setup(bot: Genji) -> None:
    """Add SettingsCog to bot."""
    await bot.add_cog(SettingsCog(bot))
