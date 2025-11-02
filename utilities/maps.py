from __future__ import annotations

import re
from logging import getLogger
from typing import TYPE_CHECKING, TypeVar, cast, get_args

import discord
import msgspec
from genjipk_sdk.models import Creator, MapCreateDTO, MapReadDTO, Medals
from genjipk_sdk.utilities import (
    DIFFICULTY_RANGES_ALL,
    PLAYTEST_VOTE_THRESHOLD,
    DifficultyAll,
    convert_raw_difficulty_to_difficulty_top,
    get_map_banner,
)
from genjipk_sdk.utilities._types import MapCategory, Mechanics, OverwatchCode, OverwatchMap, Restrictions

from utilities.base import BaseView
from utilities.emojis import VERIFIED_BRONZE, VERIFIED_GOLD, VERIFIED_SILVER
from utilities.extra import poll_job_until_complete
from utilities.formatter import FilteredFormatter

if TYPE_CHECKING:
    from utilities._types import GenjiItx

T = TypeVar("T")


CODE_VERIFICATION = re.compile(r"^[A-Z0-9]{4,6}$")


log = getLogger(__name__)


def _remove_nulls(sequence: list[T] | None) -> list[T]:
    """Remove None values from a list.

    Args:
        sequence (list[T] | None): A list that may contain None values.

    Returns:
        list[T]: A new list with all None values removed.
    """
    if sequence is None:
        return []
    return [x for x in sequence if x is not None]


class MapCreateModel(MapCreateDTO):
    def to_format_dict(self) -> dict[str, str | None]:
        """Return a dictionary representation for Formatter interpolation.

        Returns:
            dict[str, str | None]: A mapping of field labels to stringified values.
        """
        _mechanics = _remove_nulls(self.mechanics)
        _restrictions = _remove_nulls(self.restrictions)
        _medals = (
            ""
            if not self.medals
            else (
                f"{VERIFIED_GOLD} {self.medals.gold} | "
                f"{VERIFIED_SILVER} {self.medals.silver} | "
                f"{VERIFIED_BRONZE} {self.medals.bronze}"
            )
        )
        return {
            "Code": self.code,
            "Map": self.map_name,
            "Category": self.category,
            "Checkpoints": str(self.checkpoints),
            "Difficulty": self.difficulty,
            "Mechanics": ", ".join(_mechanics),
            "Restrictions": ", ".join(_restrictions),
            "Guide": f"[Link]({self.guide_url})" if self.guide_url else "",
            "Medals": _medals,
            "Desc": self.description,
        }

    @property
    def map_banner(self) -> str:
        """Get a custom banner if set, otherwise generate one.

        Returns:
            str: Banner URL.
        """
        if self.custom_banner:
            return self.custom_banner
        return get_map_banner(self.map_name)


class MapModel(MapReadDTO):
    override_finalize: bool | None = None

    def to_format_dict(self) -> dict[str, str | None]:
        """Return a dictionary representation for Formatter interpolation.

        Returns:
            dict[str, str | None]: A mapping of field labels to stringified values.
        """
        creator_names = [creator.name for creator in self.creators]
        _mechanics = _remove_nulls(self.mechanics)
        _restrictions = _remove_nulls(self.restrictions)
        _guides = [f"[Link {i}]({link})" for i, link in enumerate(self.guides or [], 1) if link]
        _medals = (
            ""
            if not self.medals
            else (
                f"{VERIFIED_GOLD} {self.medals.gold} | "
                f"{VERIFIED_SILVER} {self.medals.silver} | "
                f"{VERIFIED_BRONZE} {self.medals.bronze}"
            )
        )
        return {
            "Code": self.code,
            "Title": self.title,
            "Creator": discord.utils.escape_markdown(", ".join(creator_names)),
            "Map": self.map_name,
            "Category": self.category,
            "Checkpoints": str(self.checkpoints),
            "Difficulty": self.difficulty,
            "Mechanics": ", ".join(_mechanics) if _mechanics else None,
            "Restrictions": ", ".join(_restrictions) if _mechanics else None,
            "Guide": ", ".join(_guides),
            "Medals": _medals,
            "Desc": self.description,
        }

    @property
    def finalizable(self) -> bool:
        """Determine if the map can be finalized based on vote thresholds.

        Returns:
            bool: Whether the map is eligible for finalization.

        Raises:
            AttributeError: If no playtest data is attached.
        """
        if self.override_finalize is True:
            log.debug("Finalizable: Override is true=True")
            return True

        if not self.playtest:
            raise AttributeError("This data does not have a playtest attached.")

        if not self.playtest.vote_count:
            log.debug("Finalizable: Vote count is 0=false")
            return False

        if self.playtest.vote_count >= self.playtest_threshold:
            log.debug("Finalizable: Hit the threshold=true")
            return True

        if self.override_finalize in (False, None):
            log.debug("Finalizable: Override is false=false")
            return False

        log.debug("Finalizable: None of the above=false")
        return False

    @property
    def playtest_threshold(self) -> int:
        if not self.playtest:
            raise AttributeError("This data does not have a playtest attached.")
        _diff = convert_raw_difficulty_to_difficulty_top(self.playtest.initial_difficulty)
        return PLAYTEST_VOTE_THRESHOLD[_diff]


class PartialMapCreateModel(msgspec.Struct):
    code: OverwatchCode
    map_name: OverwatchMap
    checkpoints: int
    category: MapCategory
    creator_id: int
    creator_name: str
    gold: float | None
    silver: float | None
    bronze: float | None
    description: str | None = None
    guide_url: str | None = None
    title: str | None = None
    map_banner: str | None = None

    def __post_init__(self) -> None:
        """Set the banner if not explicitly provided."""
        if not self.map_banner:
            self.map_banner = get_map_banner(self.map_name)


class ContinueButton(discord.ui.Button["MapSubmissionView"]):
    view: "MapSubmissionView"

    def __init__(self, disabled: bool = True) -> None:
        """Initialize the continue button.

        Args:
            disabled (bool, optional): Whether the button starts disabled. Defaults to True.
        """
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Continue",
            disabled=disabled,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Handle continue button click by validating data and showing confirmation view.

        Args:
            itx (GenjiItx): The interaction context.
        """
        new_data = MapCreateModel(
            code=self.view.data.code,
            map_name=self.view.data.map_name,
            category=self.view.data.category,
            creators=[Creator(self.view.data.creator_id, True)],
            checkpoints=self.view.data.checkpoints,
            difficulty=cast("DifficultyAll", self.view.difficulty_select.values[0]),
            guide_url=self.view.data.guide_url,
            mechanics=cast("list[Mechanics]", self.view.mechanics_select.values),
            restrictions=cast("list[Restrictions]", self.view.restrictions_select.values),
            description=self.view.data.description,
            medals=Medals(self.view.data.gold, self.view.data.silver, self.view.data.bronze)
            if self.view.data.gold and self.view.data.silver and self.view.data.bronze
            else None,
            title=self.view.data.title,
            custom_banner=self.view.data.map_banner,
        )
        self.view.stop()
        view = MapSubmissionConfirmationView(new_data)
        await itx.response.edit_message(view=view)
        view.original_interaction = itx


class SubmitButton(discord.ui.Button["MapSubmissionConfirmationView"]):
    view: "MapSubmissionConfirmationView"

    def __init__(self) -> None:
        """Initialize the submit button."""
        super().__init__(style=discord.ButtonStyle.green, label="Submit")

    async def callback(self, itx: GenjiItx) -> None:
        """Submit the map via API call and notify the user.

        Args:
            itx (GenjiItx): The interaction context.
        """
        for c in self.view.walk_children():
            if isinstance(c, discord.ui.Button):
                c.disabled = True
        await itx.response.edit_message(view=self.view)

        await itx.followup.send("Please wait while we process this request.", ephemeral=True)

        screenshot = self.view.custom_banner_attachment

        if screenshot:
            screenshot_url = await itx.client.api.upload_image(
                await screenshot.read(),
                filename=screenshot.filename,
                content_type=screenshot.content_type or "image/png",
            )
            self.view.data.custom_banner = screenshot_url

        res = await itx.client.api.submit_map(self.view.data)
        self.view.stop()
        job_status = res.job_status
        assert job_status
        job = await poll_job_until_complete(itx.client.api, job_status.id)

        if not job:
            log.debug(f"Timed out waiting for job. {job_status.id}")
            await itx.followup.send(
                content=(
                    "There was an unknown error while processing. This has been logged. "
                    "Please do not try again until it has been resolved.\n"
                    f"{self.view.data.code}\n"
                ),
                ephemeral=True,
            )
        elif job.status == "succeeded":
            log.debug(f"Job completed successfully! {job_status.id}")
            await itx.followup.send(
                content="Map submission was successful.\n",
                ephemeral=True,
            )
        else:
            log.debug(f"Job ({job_status.id}) ended with status: {job.status}")
            await itx.followup.send(
                content=(
                    "There was an unknown error while processing. This has been logged. "
                    "Please do not try again until it has been resolved.\n"
                ),
                ephemeral=True,
            )


class CancelButton(discord.ui.Button):
    def __init__(self) -> None:
        """Initialize the cancel button."""
        super().__init__(
            style=discord.ButtonStyle.red,
            label="Cancel",
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Cancel the submission process and notify the user.

        Args:
            itx (GenjiItx): The interaction context.
        """
        await itx.response.send_message(
            "The submission process has been cancelled. Please use that command again to submit.",
            ephemeral=True,
        )
        assert self.view
        self.view.stop()
        await itx.delete_original_response()


class MapSubmissionView(BaseView):
    def __init__(self, data: PartialMapCreateModel, custom_banner_attachment: discord.Attachment | None = None) -> None:
        """Initialize the map submission view.

        Args:
            data (PartialMapCreateModel): Initial form input.
            custom_banner_attachment (discord.Attachment): The Attachment for a custom banner.
        """
        self.difficulty_select = DifficultySelect()
        self.mechanics_select = MechanicsSelect()
        self.restrictions_select = RestrictionsSelect()

        self.continue_button = ContinueButton()
        self.cancel_button = CancelButton()
        self.data = data
        self.custom_banner_attachment = custom_banner_attachment
        super().__init__()

    def rebuild_components(
        self,
    ) -> None:
        """Rebuild the UI components for map submission input."""
        self.clear_items()
        container = discord.ui.Container(
            discord.ui.TextDisplay("## Additional Information"),
            discord.ui.ActionRow(self.difficulty_select),
            discord.ui.ActionRow(self.mechanics_select),
            discord.ui.ActionRow(self.restrictions_select),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"# {self._end_time_string}"),
            discord.ui.ActionRow(self.continue_button, self.cancel_button),
        )
        self.add_item(container)


class MapSubmissionConfirmationView(BaseView):
    def __init__(self, data: MapCreateModel, custom_banner_attachment: discord.Attachment | None = None) -> None:
        """Initialize the confirmation view.

        Args:
            data (MapCreateModel): Finalized map data.
            custom_banner_attachment (discord.Attachment): The Attachment for a custom banner.
        """
        self.data = data
        self.custom_banner_attachment = custom_banner_attachment
        self.submit_button = SubmitButton()
        self.cancel_button = CancelButton()
        super().__init__()

    def rebuild_components(
        self,
    ) -> None:
        """Rebuild the confirmation UI with a preview and submit/cancel buttons."""
        self.clear_items()
        assert self.data.map_banner
        container = discord.ui.Container(
            *((discord.ui.TextDisplay(self.data.title),) if self.data.title else ()),
            discord.ui.TextDisplay(FilteredFormatter(self.data).format()),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    media=self.data.map_banner,
                )
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"# {self._end_time_string}"),
            discord.ui.ActionRow(self.submit_button, self.cancel_button),
        )
        self.add_item(container)


class DifficultySelect(discord.ui.Select[MapSubmissionView]):
    view: MapSubmissionView

    def __init__(self) -> None:
        """Initialize the difficulty dropdown."""
        _options = [discord.SelectOption(value=x, label=x) for x in DIFFICULTY_RANGES_ALL]
        super().__init__(
            placeholder="Select Difficulty",
            options=_options,
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Handle difficulty selection and enable the continue button.

        Args:
            itx (GenjiItx): The interaction context.
        """
        for option in self.options:
            option.default = option.value in self.values
        if self.view.continue_button.disabled:
            self.view.continue_button.disabled = False
        await itx.response.edit_message(view=self.view)


class MechanicsSelect(discord.ui.Select):
    def __init__(self) -> None:
        """Initialize the mechanics multi-select."""
        _options = [discord.SelectOption(value=x, label=x) for x in get_args(Mechanics)]
        super().__init__(
            placeholder="Select Mechanics",
            options=_options,
            max_values=len(_options),
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Update selected mechanics in the view.

        Args:
            itx (GenjiItx): The interaction context.
        """
        for option in self.options:
            option.default = option.value in self.values
        await itx.response.edit_message(view=self.view)


class RestrictionsSelect(discord.ui.Select):
    def __init__(self) -> None:
        """Initialize the restrictions multi-select."""
        _options = [discord.SelectOption(value=x, label=x) for x in get_args(Restrictions)]
        super().__init__(
            placeholder="Select Restrictions",
            options=_options,
            max_values=len(_options),
        )

    async def callback(self, itx: GenjiItx) -> None:
        """Update selected restrictions in the view.

        Args:
            itx (GenjiItx): The interaction context.
        """
        for option in self.options:
            option.default = option.value in self.values
        await itx.response.edit_message(view=self.view)
