from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

from discord import ButtonStyle, SelectOption, ui
from genjipk_sdk.utilities import DifficultyAll

from ..base import BaseView, ConfirmationButton, ConfirmationCancelButton

if TYPE_CHECKING:
    from .._types import GenjiItx
    from ..maps import MapModel


class ModStatusButton(ui.Button):
    def __init__(self, enabled: bool, label: Literal["hidden", "official", "archived", "playtesting"]) -> None:
        """Initialize a toggleable moderation status button.

        Args:
            enabled (bool): Whether the status is currently enabled.
            status (Literal): The map status this button controls.
        """
        self.enabled = enabled
        self._status = label
        super().__init__()
        self._rebuild()

    async def callback(self, itx: GenjiItx) -> None:
        """Toggle the button state and update the view.

        Args:
            itx (GenjiItx): The interaction triggered by the button click.
        """
        self.enabled = not self.enabled
        self._rebuild()
        await itx.response.edit_message(view=self.view)

    def _rebuild(self) -> None:
        """Rebuild the button label and style based on the current state."""
        name = self._status.capitalize()
        self.label = name if self.enabled else f"Not {name}"
        self.style = ButtonStyle.green if self.enabled else ButtonStyle.red


class ModPlaytestSendToPlaytestButton(ui.Button["ModStatusView"]):
    def __init__(self) -> None:
        """Initialize the ModPlaytestSendToPlaytest button.

        Args:
            enabled (PlaytestStatus): The currently selected playtest status.
        """
        self.enabled = False
        super().__init__(style=ButtonStyle.red, label="Send to playtest DISABLED")
        self._rebuild()

    async def callback(self, itx: GenjiItx) -> None:
        """Update the selected playtest status and refresh the view.

        Args:
            itx (GenjiItx): The interaction triggered by the dropdown change.
        """
        self.enabled = not self.enabled
        self._rebuild()
        assert self.view
        self.view.playtest_difficulty_select.disabled = not self.enabled
        self.view.confirmation_button.disabled = not (self.enabled or self.view.playtest_difficulty_select.values)
        await itx.response.edit_message(view=self.view)

    def _rebuild(self) -> None:
        """Rebuild the button label and style based on the current state."""
        self.label = "Send to playtest ENABLED" if self.enabled else "Send to playtest DISABLED"
        self.style = ButtonStyle.green if self.enabled else ButtonStyle.red


class PlaytestDifficultySelect(ui.Select["ModStatusView"]):
    def __init__(self, disabled: bool = True) -> None:
        """Initialize PlaytestDifficultySelect."""
        super().__init__(
            placeholder="Select the difficulty for sending back to playtest.",
            options=[SelectOption(label=d, value=d) for d in get_args(DifficultyAll)],
            disabled=disabled,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Set the return to playtest difficulty."""
        for option in self.options:
            option.default = option.value in self.values
        assert self.view
        self.view.confirmation_button.disabled = not (self.view.playtest_button.enabled or self.values)
        await itx.response.edit_message(view=self.view)


class ModStatusView(BaseView):
    def __init__(self, data: MapModel) -> None:
        """Initialize the moderation status view.

        Args:
            data (MapModel): The map data used to initialize the status controls.
        """
        self._data = data
        self.confirmed = None
        self.hidden_button = ModStatusButton(self._data.hidden, "hidden")
        self.official_button = ModStatusButton(self._data.official, "official")
        self.archived_button = ModStatusButton(self._data.archived, "archived")
        self.playtest_button = ModPlaytestSendToPlaytestButton()
        self.playtest_difficulty_select = PlaytestDifficultySelect()
        self.confirmation_button = ConfirmationButton()
        super().__init__()

    def rebuild_components(self) -> None:
        """Build and add all UI components for editing map statuses."""
        self.clear_items()
        playtest_section = (
            (
                ui.Section(
                    ui.TextDisplay(
                        "**Send map to playtest**.\nThis will convert all records for this map into legacy records."
                    ),
                    accessory=self.playtest_button,
                ),
                ui.ActionRow(self.playtest_difficulty_select),
            )
            if self._data.playtesting
            else ()
        )
        container = ui.Container(
            ui.TextDisplay(
                f"# Mod View - Status ({self._data.code})\n-# ⚠️ You probably don't need to use this command."
            ),
            ui.Separator(),
            ui.Section(ui.TextDisplay("Edit the **Hidden** status."), accessory=self.hidden_button),
            ui.Section(ui.TextDisplay("Edit the **Official** status."), accessory=self.official_button),
            ui.Section(ui.TextDisplay("Edit the **Archived** status."), accessory=self.archived_button),
            *playtest_section,
            ui.Separator(),
            ui.TextDisplay(f"# {self._end_time_string}"),
            ui.ActionRow(
                self.confirmation_button,
                ConfirmationCancelButton(),
            ),
        )
        self.add_item(container)

    async def confirm_callback(self) -> None:
        """Dummy confirmation callback."""
