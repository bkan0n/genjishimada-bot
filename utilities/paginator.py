from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Literal, Sequence, TypeVar

import discord
from discord import AllowedMentions, ButtonStyle, ui

from utilities.base import BaseView
from utilities.formatter import FormattableProtocol

if TYPE_CHECKING:
    from utilities._types import GenjiItx

T = TypeVar("T", bound=FormattableProtocol)


class _NextButton(ui.Button["PaginatorView"]):
    view: "PaginatorView"

    def __init__(self) -> None:
        """Initialize the Next button."""
        super().__init__(
            style=ButtonStyle.blurple,
            label="Next",
            # TODO: > Emoji
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Advance to the next page and update the view.

        Args:
            itx (GenjiItx): The interaction context.
        """
        self.view.increment_page_index()
        await itx.response.edit_message(view=self.view, allowed_mentions=AllowedMentions.none())


class _PreviousButton(ui.Button["PaginatorView"]):
    view: "PaginatorView"

    def __init__(self) -> None:
        """Initialize the Previous button."""
        super().__init__(
            style=ButtonStyle.blurple,
            label="Previous",
            # TODO: < Emoji
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Initialize the Previous button."""
        self.view.decrement_page_index()
        await itx.response.edit_message(view=self.view, allowed_mentions=AllowedMentions.none())


class PageNumberModal(discord.ui.Modal):
    number = discord.ui.TextInput(label="Number")
    value = None

    def __init__(self, limit: int) -> None:
        """Initialize the modal for entering a page number.

        Args:
            limit (int): The maximum valid page number.
        """
        super().__init__(title="Choose a page...")
        self.limit = limit
        self.number.placeholder = f"Must be an integer in range 1 - {self.limit}"

    async def on_submit(self, itx: GenjiItx) -> None:
        """Handle modal submission and validate input.

        Args:
            itx (GenjiItx): The interaction context.

        Raises:
            TypeError: If the entered value is not a valid integer within the limit.
        """
        await itx.response.defer(ephemeral=True, thinking=True)

        try:
            self.value = int(self.number.value)
            if not 1 <= self.value <= self.limit:
                raise ValueError("Value out of range.")
        except ValueError:
            raise TypeError("Invalid integer.")

        if self.value:
            await itx.delete_original_response()


class _PageNumberButton(ui.Button["PaginatorView"]):
    view: "PaginatorView"

    def __init__(self, total_pages: int) -> None:
        """Initialize the page number button.

        Args:
            total_pages (int): Total number of pages in the paginator.
        """
        super().__init__(
            style=ButtonStyle.grey,
            label=f"1/{total_pages}",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Open modal to jump to a specific page number.

        Args:
            itx (GenjiItx): The interaction context.
        """
        modal = PageNumberModal(len(self.view.pages))
        await itx.response.send_modal(modal)
        await modal.wait()
        number = int(modal.number.value)
        self.view.skip_to_page_index(number - 1)
        await itx.edit_original_response(view=self.view, allowed_mentions=AllowedMentions.none())


class PaginatorView(BaseView, Generic[T]):
    def __init__(
        self,
        title: str,
        data: Sequence[T],
        *,
        page_size: int = 5,
    ) -> None:
        """Initialize a paginated view.

        Args:
            title (str): Title to display at the top of the paginator.
            data (Sequence[T]): The data to paginate.
            page_size (int, optional): Number of items per page. Defaults to 5.
        """
        self._page_size = page_size
        self._title = title
        self.rebuild_data(data)

        super().__init__(timeout=600)

    @property
    def pages(self) -> list[list[T]]:
        """list[list[T]]: Chunked pages built from input data."""
        return self._pages

    @property
    def current_page_index(self) -> int:
        """int: The index of the currently active page."""
        return self._current_page_index

    @property
    def current_page(self) -> list[T]:
        """list[T]: The current page's content."""
        return self._pages[self._current_page_index]

    def _get_requested_index(self, value: Literal[-1, 1]) -> int:
        """Calculate the new page index by increment or decrement with wraparound.

        Args:
            value (Literal[-1, 1]): Direction to move.

        Returns:
            int: New page index.
        """
        length = len(self._pages)
        return (self._current_page_index + value) % length

    def increment_page_index(self) -> None:
        """Increment the current page index and refresh the view."""
        self._current_page_index = self._get_requested_index(1)
        self._page_number_button.label = f"{self._current_page_index + 1}/{len(self._pages)}"
        self.rebuild_components()

    def decrement_page_index(self) -> None:
        """Decrement the current page index and refresh the view."""
        self._current_page_index = self._get_requested_index(-1)
        self._page_number_button.label = f"{self._current_page_index + 1}/{len(self._pages)}"
        self.rebuild_components()

    def skip_to_page_index(self, value: int) -> None:
        """Jump directly to a specific page index.

        Args:
            value (int): The target page index (0-based).
        """
        self._current_page_index = value % len(self._pages)
        self._page_number_button.label = f"{self._current_page_index + 1}/{len(self._pages)}"
        self.rebuild_components()

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build the display section for the current page.

        Returns:
            Sequence[ui.Item]: The UI items for the page.

        Raises:
            NotImplementedError: Must be implemented by subclasses.
        """
        raise NotImplementedError

    def build_additional_action_row(self) -> ui.ActionRow | None:
        """Build an additional action row under the pagination buttons."""
        return

    def rebuild_components(self) -> None:
        """Rebuild all components for the current page."""
        self.clear_items()
        body = self.build_page_body()

        action_row = ()
        if len(self.pages) > 1:
            action_row = (
                ui.ActionRow(
                    self._previous_button,
                    self._page_number_button,
                    self._next_button,
                ),
            )

        additional_action_row = ()
        if row := self.build_additional_action_row():
            additional_action_row = (
                ui.Separator(),
                row,
            )

        container = ui.Container(
            ui.TextDisplay(f"# {self._title}"),
            ui.Separator(),
            *body,
            ui.TextDisplay(f"# {self._end_time_string}"),
            *action_row,
            *additional_action_row,
        )
        self.add_item(container)

    @property
    def item_index_offset(self) -> int:
        """int: The starting global index offset for the current page.

        This value represents how far into the overall dataset the
        current page begins. For example, with a page size of 5:

        - Page 0 → offset 0
        - Page 1 → offset 5
        - Page 2 → offset 10

        When enumerating items on the current page, add this offset
        to each local index to get the global index across all pages.
        """
        return self._current_page_index * self._page_size

    def rebuild_data(self, data: Sequence[T]) -> None:
        """Rebuild paginated data and reset pagination state.

        Args:
            data (Sequence[T]): Data to paginate.
        """
        self._pages = list(discord.utils.as_chunks(data, self._page_size))
        self._current_page_index = 0

        self._previous_button = _PreviousButton()
        self._page_number_button = _PageNumberButton(len(self.pages))
        self._next_button = _NextButton()
