from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import msgspec
from discord import ButtonStyle, ui
from discord.app_commands import AppCommandError
from genjipk_sdk.maps import MAX_CREATORS, MapPatchRequest
from genjipk_sdk.users import Creator, CreatorFull, UserResponse

from utilities.base import BaseView, ConfirmationView
from utilities.formatter import FilteredFormatter

if TYPE_CHECKING:
    from utilities._types import GenjiItx
    from utilities.maps import MapModel


class FormattableCreator(CreatorFull):
    def to_format_dict(self) -> dict:
        """Convert the creator to a dictionary for formatted rendering.

        Returns:
            dict: Mapping of display labels to user data.
        """
        return {
            "User": self.mention_user,
            "User ID": self.id,
            "Also Known As": self.name,
        }

    @property
    def mention_user(self) -> str:
        """Mention string for this Discord user.

        Returns:
            str: A Discord-formatted user mention.
        """
        return f"<@{self.id}>"


class FormattableUser(UserResponse):
    def to_format_dict(self) -> dict:
        """Convert the creator to a dictionary for formatted rendering.

        Returns:
            dict: Mapping of display labels to user data.
        """
        overwatch_usernames = self.overwatch_usernames or []
        aka = ", ".join([self.global_name, self.nickname, *overwatch_usernames])
        return {
            "User": self.mention_user,
            "User ID": self.id,
            "Also Known As": aka,
        }

    @property
    def mention_user(self) -> str:
        """Mention string for this Discord user.

        Returns:
            str: A Discord-formatted user mention.
        """
        return f"<@{self.id}>"


class EditCreatorIDModal(ui.Modal):
    user_id = ui.TextInput(label="Creator Discord ID")

    def __init__(
        self, data: MapModel, creator: FormattableCreator, original_itx: GenjiItx, original_view: MapCreatorModView
    ) -> None:
        """Initialize the modal for editing a creator's Discord ID.

        Args:
            data (MapModel): Map data for context.
            creator (FormattableCreator): The creator being edited.
            original_itx (GenjiItx): The original interaction that opened this modal.
            original_view (MapCreatorModView): The view this modal belongs to.
        """
        super().__init__(title="Edit Creator ID")
        self._data = data
        self._creator = creator
        self._original_itx = original_itx
        self._original_view = original_view

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle submission of the modal and optionally apply the creator ID change.

        Args:
            itx (GenjiItx): The interaction context from the modal submission.

        Raises:
            ValueError: If the provided user ID is invalid or already associated with the map.
        """
        await itx.response.defer(ephemeral=True, thinking=True)
        user = await itx.client.api.get_user(int(self.user_id.value))
        if not user:
            await itx.edit_original_response(content="The User ID does not seem to be valid or exist.")
            return
        if user.id in {c.id for c in self._data.creators}:
            await itx.edit_original_response(content="The User ID is already associated with this map.")
            return

        async def confirm_callback() -> None:
            new_creators = msgspec.convert(
                [x for x in self._data.creators if x.id != self._creator.id], list[Creator], from_attributes=True
            )
            edited_creator = Creator(id=int(self.user_id.value), is_primary=self._creator.is_primary)
            new_creators.append(edited_creator)
            new_data = MapPatchRequest(creators=new_creators)
            await itx.client.api.edit_map(self._data.code, data=new_data)
            await self._original_view.refresh_data(self._original_itx)

        formatted_user = FilteredFormatter(msgspec.convert(user, FormattableUser, from_attributes=True)).format()
        formatted_creator = FilteredFormatter(self._creator).format()
        message = (
            "Are you sure you change the ID of this creator?\n\n"
            f"Original:\n {formatted_creator}\nNew Creator:\n{formatted_user}"
        )
        view = ConfirmationView(message, confirm_callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx


class AddCreatorIDModal(ui.Modal):
    user_id = ui.TextInput(label="Creator Discord ID")

    def __init__(self, data: MapModel, original_itx: GenjiItx, original_view: MapCreatorModView) -> None:
        """Initialize the modal for adding a new creator to the map.

        Args:
            data (MapModel): Map data for context.
            original_itx (GenjiItx): The original interaction that opened this modal.
            original_view (MapCreatorModView): The view this modal belongs to.
        """
        super().__init__(title="Add Creator ID")
        self._data = data
        self._original_itx = original_itx
        self._original_view = original_view

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle submission of the modal and optionally add the new creator.

        Args:
            itx (GenjiItx): The interaction context from the modal submission.

        Raises:
            ValueError: If the user ID is invalid or already exists as a creator.
        """
        await itx.response.defer(ephemeral=True, thinking=True)
        user = await itx.client.api.get_user(int(self.user_id.value))
        if not user:
            await itx.edit_original_response(content="The User ID does not seem to be valid or exist.")
            return
        if user.id in {c.id for c in self._data.creators}:
            await itx.edit_original_response(content="The User ID is already associated with this map.")
            return

        async def confirm_callback() -> None:
            new_creators = msgspec.convert(self._data.creators, list[Creator], from_attributes=True)
            edited_creator = Creator(id=int(self.user_id.value), is_primary=False)
            new_creators.append(edited_creator)
            new_data = MapPatchRequest(creators=new_creators)
            await itx.client.api.edit_map(self._data.code, data=new_data)
            await self._original_view.refresh_data(self._original_itx)

        formatted_user = FilteredFormatter(msgspec.convert(user, FormattableUser, from_attributes=True)).format()
        message = f"Are you sure you want to add this user as a creator?\n\nNew Creator:\n{formatted_user}"
        view = ConfirmationView(message, confirm_callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx


class EditCreatorButton(ui.Button["MapCreatorModView"]):
    view: "MapCreatorModView"

    def __init__(self, data: MapModel, creator: FormattableCreator) -> None:
        """Create a button to edit the creator's ID.

        Args:
            data (MapModel): The map context.
            creator (FormattableCreator): The creator to edit.
        """
        super().__init__(label="Edit ID", style=ButtonStyle.green)
        self._data = data
        self._creator = creator

    async def callback(self, itx: GenjiItx) -> None:
        """Open the edit creator modal.

        Args:
            itx (GenjiItx): The button interaction context.
        """
        assert self.view.original_interaction
        modal = EditCreatorIDModal(self._data, self._creator, self.view.original_interaction, self.view)
        await itx.response.send_modal(modal)


class AddCreatorButton(ui.Button["MapCreatorModView"]):
    view: "MapCreatorModView"

    def __init__(self, data: MapModel) -> None:
        """Create a button to add a new creator.

        Args:
            data (MapModel): The map context.
        """
        super().__init__(label="Add Creator ID", style=ButtonStyle.green)
        self._data = data

    async def callback(self, itx: GenjiItx) -> None:
        """Open the add creator modal.

        Args:
            itx (GenjiItx): The button interaction context.
        """
        assert self.view.original_interaction
        modal = AddCreatorIDModal(self._data, self.view.original_interaction, self.view)
        await itx.response.send_modal(modal)


class RemoveCreatorButton(ui.Button["MapCreatorModView"]):
    view: "MapCreatorModView"

    def __init__(self, data: MapModel, creator: FormattableCreator) -> None:
        """Create a button to remove a creator from the map.

        Args:
            data (MapModel): The map context.
            creator (FormattableCreator): The creator to remove.
        """
        super().__init__(label="Delete", style=ButtonStyle.red)
        self._data = data
        self._creator = creator

    async def callback(self, itx: GenjiItx) -> None:
        """Prompt for confirmation to remove a creator.

        Args:
            itx (GenjiItx): The button interaction context.
        """

        async def confirm_callback() -> None:
            new_creators = msgspec.convert(
                [x for x in self._data.creators if x.id != self._creator.id],
                list[Creator],
                from_attributes=True,
            )
            new_data = MapPatchRequest(creators=new_creators)
            await itx.client.api.edit_map(self._data.code, data=new_data)
            assert self.view.original_interaction
            await self.view.refresh_data(self.view.original_interaction)

        formatted_data = FilteredFormatter(self._creator).format()
        message = f"Are you sure you want to remove this creator?\n\n{formatted_data}"

        view = ConfirmationView(message, confirm_callback)
        await itx.response.send_message(view=view, ephemeral=True)
        view.original_interaction = itx


class SetPrimaryCreatorButton(ui.Button["MapCreatorModView"]):
    view: "MapCreatorModView"

    def __init__(self, data: MapModel, creator: FormattableCreator) -> None:
        """Create a button to set this creator as the primary one.

        Args:
            data (MapModel): The map context.
            creator (FormattableCreator): The creator to promote to primary.
        """
        super().__init__(label="Set Primary", style=ButtonStyle.blurple)
        self._data = data
        self._creator = creator

    async def callback(self, itx: GenjiItx) -> None:
        """Set this creator as the map's primary creator.

        Args:
            itx (GenjiItx): The button interaction context.
        """
        await itx.response.defer(ephemeral=True, thinking=False)
        new_creators = msgspec.convert(self._data.creators, list[Creator], from_attributes=True)
        for c in new_creators:
            c.is_primary = c.id == self._creator.id
        new_data = MapPatchRequest(creators=new_creators)
        await itx.client.api.edit_map(self._data.code, data=new_data)
        assert self.view.original_interaction
        await self.view.refresh_data(self.view.original_interaction)


class MapCreatorModView(BaseView):
    def __init__(self, data: MapModel) -> None:
        """Initialize the moderation view for editing map creators.

        Args:
            data (MapModel): The map whose creators will be modified.
        """
        self._data = data
        super().__init__()

    def rebuild_components(self) -> None:
        """Rebuild the view with creator sections, buttons, and separators."""
        self.clear_items()
        add_creator_button = (
            (ui.ActionRow(AddCreatorButton(self._data)),) if len(self._data.creators) < MAX_CREATORS else set()
        )
        creators = []
        for creator in self._data.creators:
            formattable_creator = FormattableCreator(
                id=creator.id,
                is_primary=creator.is_primary,
                name=creator.name,
            )
            conditional_buttons = (
                (
                    SetPrimaryCreatorButton(self._data, formattable_creator),
                    RemoveCreatorButton(self._data, formattable_creator),
                )
                if not creator.is_primary
                else set()
            )
            action_row = (
                (
                    ui.ActionRow(
                        *conditional_buttons,
                        EditCreatorButton(self._data, formattable_creator),
                    ),
                )
                if len(self._data.creators) > 1
                else set()
            )
            section = (
                ui.TextDisplay(FilteredFormatter(formattable_creator).format()),
                *action_row,
                ui.Separator(),
            )
            creators.extend(section)

        container = ui.Container(
            ui.TextDisplay(f"# Mod View - Creators ({self._data.code})\n-# Maximum of three creators per map."),
            *creators,
            *add_creator_button,
            ui.Separator(),
            ui.TextDisplay(f"# {self._end_time_string}"),
        )
        self.add_item(container)

    async def refresh_data(self, itx: GenjiItx) -> None:
        """Refresh the internal map data and update the view.

        Args:
            itx (GenjiItx): The interaction triggering the refresh.
        """
        data = await itx.client.api.get_map(code=self._data.code)
        self._data = data
        self.rebuild_components()
        await itx.edit_original_response(view=self)

    async def on_error(self, itx: GenjiItx, error: Exception, item: ui.Item[Any], /) -> None:
        """Handle errors."""
        await itx.client.tree.on_error(itx, cast("AppCommandError", error))
