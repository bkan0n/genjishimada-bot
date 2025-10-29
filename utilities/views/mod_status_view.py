from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

from discord import ButtonStyle, SelectOption, ui
from genjipk_sdk.utilities._types import PlaytestStatus

from ..base import BaseView, ConfirmationButton, ConfirmationCancelButton

if TYPE_CHECKING:
    from .._types import GenjiItx
    from ..maps import MapModel


class ModStatusButton(ui.Button):
    def __init__(self, enabled: bool, status: Literal["hidden", "official", "archived", "playtesting"]) -> None:
        """Initialize a toggleable moderation status button.

        Args:
            enabled (bool): Whether the status is currently enabled.
            status (Literal): The map status this button controls.
        """
        self._enabled = enabled
        self._status = status
        super().__init__()
        self._rebuild()

    async def callback(self, itx: GenjiItx) -> None:
        """Toggle the button state and update the view.

        Args:
            itx (GenjiItx): The interaction triggered by the button click.
        """
        self._enabled = not self._enabled
        self._rebuild()
        await itx.response.edit_message(view=self.view)

    def _rebuild(self) -> None:
        """Rebuild the button label and style based on the current state."""
        name = self._status.capitalize()
        self.label = name if self._enabled else f"Not {name}"
        self.style = ButtonStyle.green if self._enabled else ButtonStyle.red


class ModPlaytestStatusSelect(ui.Select):
    def __init__(self, initial_value: PlaytestStatus) -> None:
        """Initialize the playtest status dropdown selector.

        Args:
            initial_value (PlaytestStatus): The currently selected playtest status.
        """
        super().__init__(
            options=[SelectOption(label=s, value=s, default=s == initial_value) for s in get_args(PlaytestStatus)],
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Update the selected playtest status and refresh the view.

        Args:
            itx (GenjiItx): The interaction triggered by the dropdown change.
        """
        for option in self.options:
            option.default = option.value in self.values
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
        self.playtest_select = ModPlaytestStatusSelect(self._data.playtesting)
        super().__init__()

    def rebuild_components(self) -> None:
        """Build and add all UI components for editing map statuses."""
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay(
                f"# Mod View - Status ({self._data.code})\n-# ⚠️ You probably don't need to use this command."
            ),
            ui.Separator(),
            ui.Section(ui.TextDisplay("Edit the **Hidden** status."), accessory=self.hidden_button),
            ui.Section(ui.TextDisplay("Edit the **Official** status."), accessory=self.official_button),
            ui.Section(ui.TextDisplay("Edit the **Archived** status."), accessory=self.archived_button),
            ui.TextDisplay("Edit the **Playtesting** status."),
            ui.ActionRow(self.playtest_select),
            ui.Separator(),
            ui.TextDisplay(f"# {self._end_time_string}"),
            ui.ActionRow(
                ConfirmationButton(),
                ConfirmationCancelButton(),
            ),
        )
        self.add_item(container)
