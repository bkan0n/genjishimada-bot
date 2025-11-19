from __future__ import annotations

import re
from logging import getLogger
from typing import TYPE_CHECKING, Sequence

from discord import ButtonStyle, ui
from genjipk_sdk.maps import URL_REGEX, GuideFullResponse, OverwatchCode

from utilities.base import ConfirmationView
from utilities.formatter import FilteredFormatter
from utilities.paginator import PaginatorView

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx

log = getLogger(__name__)


class FormattableGuide(GuideFullResponse):
    code: OverwatchCode | None = None
    thumbnail: str | None = None

    def to_format_dict(self) -> dict:
        """Convert the struct to a dictionary for rendering.

        Returns:
            dict[str, str | None]: Mapping of field names to values.
        """
        return {
            "User": self.mention_user,
            "User ID": self.user_id,
            "Also Known As": ", ".join(self.usernames),
        }

    @property
    def mention_user(self) -> str:
        """Generate a user mention string based on the guide's user ID."""
        return f"<@{self.user_id}>"


class EditGuideURLModal(ui.Modal):
    url = ui.TextInput(label="URL")

    def __init__(self, guide: FormattableGuide, original_itx: GenjiItx, original_view: ModGuidePaginatorView) -> None:
        """Initialize the modal for editing a guide's URL.

        Args:
            guide (FormattableGuide): The guide being edited.
            original_itx (GenjiItx): The original interaction that triggered this modal.
            original_view (ModGuidePaginatorView): The paginator view where the modal was opened.
        """
        super().__init__(title="Edit Guide URL")
        self._guide = guide
        self._original_itx = original_itx
        self._original_view = original_view

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle the submission of a new guide URL.

        Args:
            itx (GenjiItx): The interaction from the modal submission.
        """
        if not re.match(URL_REGEX, self.url.value):
            await itx.response.send_message(
                "The URL does not seem to be valid.",
                ephemeral=True,
            )
            return

        async def confirm_callback() -> None:
            assert self._guide.code
            await itx.client.api.edit_guide(self._guide.code, self._guide.user_id, self.url.value)
            await self._original_view.refresh_data(self._original_itx)

        formatted_data = FilteredFormatter(self._guide).format()
        message = (
            "Are you sure you want to edit this guide?\n\nOriginal:\n"
            f"{formatted_data}\nOld URL: {self._guide.url}\nNew URL: {self.url.value}"
        )
        await itx.response.send_message(view=ConfirmationView(message, confirm_callback), ephemeral=True)


class EditGuideButton(ui.Button["ModGuidePaginatorView"]):
    view: "ModGuidePaginatorView"

    def __init__(self, guide: FormattableGuide) -> None:
        """Initialize the edit button for a guide.

        Args:
            guide (FormattableGuide): The guide to be edited.
        """
        super().__init__(label="Edit", style=ButtonStyle.green)
        self._guide = guide

    async def callback(self, itx: GenjiItx) -> None:
        """Open the guide editing modal when the button is clicked.

        Args:
            itx (GenjiItx): The interaction from clicking the button.
        """
        assert self.view.original_interaction
        assert self._guide.code
        modal = EditGuideURLModal(self._guide, self.view.original_interaction, self.view)
        await itx.response.send_modal(modal)


class DeleteGuideButton(ui.Button["ModGuidePaginatorView"]):
    view: "ModGuidePaginatorView"

    def __init__(self, guide: FormattableGuide) -> None:
        """Initialize the delete button for a guide.

        Args:
            guide (FormattableGuide): The guide to be deleted.
        """
        super().__init__(label="Delete", style=ButtonStyle.red)
        self._guide = guide

    async def callback(self, itx: GenjiItx) -> None:
        """Prompt the user to confirm and then delete the guide.

        Args:
            itx (GenjiItx): The interaction from clicking the button.
        """

        async def confirm_callback() -> None:
            assert self._guide.code
            await itx.client.api.delete_guide(self._guide.code, self._guide.user_id)
            assert self.view.original_interaction
            await self.view.refresh_data(self.view.original_interaction)

        formatted_data = FilteredFormatter(self._guide).format()
        message = f"Are you sure you want to delete this guide?\n\n{formatted_data}\n{self._guide.url}"

        view = ConfirmationView(message, confirm_callback)
        await itx.response.send_message(view=view, ephemeral=True)


class ModGuidePaginatorView(PaginatorView[FormattableGuide]):
    def __init__(
        self,
        code: str,
        data: Sequence[FormattableGuide],
        bot: Genji,
        *,
        page_size: int = 10,
    ) -> None:
        """Initialize the moderation paginator for guides.

        Args:
            code (str): The Overwatch map code the guides belong to.
            data (Sequence[FormattableGuide]): The guide data to paginate.
            bot (Genji): Bot instance
            page_size (int, optional): Number of items per page. Defaults to 10.
        """
        self._bot = bot
        self._code = code
        super().__init__(f"Mod View - Guides ({code})", data, page_size=page_size)

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build the UI components for the current page of guide entries.

        Returns:
            Sequence[ui.Item]: The list of UI components to display.
        """
        guides = self.current_page
        res = []
        for guide in guides:
            guide.code = self._code
            section = (
                ui.TextDisplay(FilteredFormatter(guide).format()),
                ui.ActionRow(
                    ui.Button(label="Open Video", style=ButtonStyle.link, url=guide.url, disabled=False),
                    EditGuideButton(guide),
                    DeleteGuideButton(guide),
                ),
                ui.Separator(),
            )
            res.extend(section)
        return res

    async def refresh_data(self, itx: GenjiItx) -> None:
        """Fetch updated guide data and refresh the paginator view.

        Args:
            itx (GenjiItx): The interaction context used to trigger the refresh.
        """
        data = await itx.client.api.get_guides(self._code)
        for guide in data:
            guide.thumbnail = await self._bot.thumbnail_service.get_thumbnail(guide.url)
        self.rebuild_data(data)
        self.rebuild_components()
        await itx.edit_original_response(view=self)
