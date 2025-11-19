from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, Sequence, TypeVar, get_args

from discord import SelectOption, ui
from genjipk_sdk.maps import Mechanics, OverwatchCode, Restrictions

from utilities.base import BaseView, ConfirmationButton, ConfirmationCancelButton

if TYPE_CHECKING:
    from utilities._types import GenjiItx

T = TypeVar("T")


class _ArrayBasedMapDetailsEditSelect[T: str](ui.Select):
    def __init__(self, values: Sequence[T], *, defaults: Iterable[T] | None = None) -> None:
        defaults_set = set(defaults) if defaults is not None else set()
        options = [SelectOption(label=val, value=val, default=(val in defaults_set)) for val in values]
        super().__init__(options=options, max_values=len(values))

    async def callback(self, itx: GenjiItx) -> None:
        await itx.response.defer(ephemeral=True)
        for option in self.options:
            option.default = option.label in self.values


class _ArrayBasedMapDetailsEditView[T: str](BaseView):
    def __init__(
        self,
        code: OverwatchCode,
        edit_type: str,
        values: Sequence[T],
        *,
        defaults: Iterable[T] | None = None,
        callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.code = code
        self.edit_type = edit_type
        self.select = _ArrayBasedMapDetailsEditSelect[T](values, defaults=defaults)
        self.confirm_callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = callback
        super().__init__()

    def rebuild_components(self) -> None:
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay(f"Edit {self.edit_type} for {self.code}"),
            ui.Separator(),
            ui.ActionRow(self.select),
            ui.ActionRow(ConfirmationButton(), ConfirmationCancelButton()),
            ui.Separator(),
            ui.TextDisplay(self._end_time_string),
        )
        self.add_item(container)


class MechanicsEditView(_ArrayBasedMapDetailsEditView[Mechanics]):
    def __init__(self, code: OverwatchCode, *, defaults: Iterable[Mechanics] | None = None) -> None:
        """Initialize the MechanicsEditView.

        This is used for mod command that require changing the mechanics of a map.

        Args:
            code (OverwatchCode): The map to edit.
            defaults (Iterable[Mechanics]): A list of Mechanics, if any.
        """
        super().__init__(code, "Mechanics", get_args(Mechanics), defaults=defaults)


class RestrictionsEditView(_ArrayBasedMapDetailsEditView[Restrictions]):
    def __init__(self, code: OverwatchCode, *, defaults: Iterable[Restrictions] | None = None) -> None:
        """Initialize the RestrictionsEditView.

        This is used for mod command that require changing the Restrictions of a map.

        Args:
            code (OverwatchCode): The map to edit.
            defaults (Iterable[Restrictions]): A list of Restrictions, if any.
        """
        super().__init__(code, "Restrictions", get_args(Restrictions), defaults=defaults)
