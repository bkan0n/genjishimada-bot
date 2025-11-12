from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, cast, get_args

from discord import ButtonStyle, app_commands, ui
from genjipk_sdk.utilities import DifficultyTop
from genjipk_sdk.utilities._types import (
    GuideURL,
    MapCategory,
    Mechanics,
    OverwatchCode,
    OverwatchMap,
    Restrictions,
)

from extensions.api_service import CompletionFilter, MedalFilter, OfficialFilter, PlaytestFilter
from utilities import transformers
from utilities.base import BaseCog
from utilities.emojis import generate_all_star_rating_strings
from utilities.formatter import FilteredFormatter
from utilities.maps import MapModel
from utilities.paginator import PaginatorView
from utilities.views.mod_guides_view import FormattableGuide

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx


class MapSearchView(PaginatorView[MapModel]):
    def __init__(self, data: Sequence[MapModel], *, page_size: int = 5) -> None:
        """Initialize MapSearchView Paginator.

        Args:
            data: A list of MapModel.
            page_size: Amount of Models on a single page.
        """
        super().__init__("Map Search", data, page_size=page_size)

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build page body for MapSearchView."""
        data = self.current_page
        res = []
        for _map in data:
            title = f"### {_map.title}" if _map.title is not None else ""
            code_block = f"\n```{_map.code}```\n"
            details = FilteredFormatter(_map, filter_fields=("Code", "Title")).format()
            section = (
                ui.TextDisplay(f"{title}{code_block}{details}"),
                ui.Separator(),
            )
            res.extend(section)
        return res


class GuideURLButton(ui.Button):
    def __init__(self, url: GuideURL) -> None:
        """Initialize a guide URL button.

        Creates a button labeled “View” that links directly to the given guide URL.

        Args:
            url: The external guide URL to link to.
        """
        super().__init__(label="View", style=ButtonStyle.url, url=url)


class MapGuideView(PaginatorView[FormattableGuide]):
    def __init__(self, code: OverwatchCode, data: Sequence[FormattableGuide], *, page_size: int = 5) -> None:
        """Initialize a map guide pagination view.

        Sets up a paginated view of guides for a specific map code. Each page
        contains up to `page_size` guides, rendered with thumbnails and buttons.

        Args:
            code: The Overwatch map code the guides belong to.
            data: The sequence of formattable guides to display.
            page_size: Number of guides per page. Defaults to 5.
        """
        self.code = code
        super().__init__(f"{code} - Guides", data, page_size=page_size)

    def build_page_body(self) -> Sequence[ui.Item]:
        """Build the paginator body."""
        data = self.current_page
        res = []
        for guide in data:
            assert guide.thumbnail
            section = (
                ui.Section(
                    ui.TextDisplay(FilteredFormatter(guide, filter_fields=["url"]).format()),
                    accessory=ui.Thumbnail(guide.thumbnail),
                ),
                ui.ActionRow(GuideURLButton(guide.url)),
                ui.Separator(),
            )
            res.extend(section)
        return res


class MapSearchCog(BaseCog):
    @app_commands.command(name="map-search")
    @app_commands.choices(
        difficulty=[app_commands.Choice(name=d, value=d) for d in get_args(DifficultyTop)],
        minimum_quality=[
            app_commands.Choice(name=s, value=i + 1) for i, s in enumerate(generate_all_star_rating_strings())
        ],
    )
    async def map_search(  # noqa: PLR0913
        self,
        itx: GenjiItx,
        map_name: app_commands.Transform[OverwatchMap, transformers.MapNameTransformer] | None,
        difficulty: app_commands.Choice[str] | None,
        code: app_commands.Transform[OverwatchCode, transformers.CodeVisibleTransformer] | None,
        creator: app_commands.Transform[int, transformers.UserTransformer] | None,
        mechanic: app_commands.Transform[Mechanics, transformers.MechanicsTransformer] | None,
        restriction: app_commands.Transform[Restrictions, transformers.RestrictionsTransformer] | None,
        minimum_quality: app_commands.Choice[int] | None,
        category: MapCategory | None,
        official_filter: OfficialFilter = "Official Only",
        completion_filter: CompletionFilter = "All",
        medal_filter: MedalFilter = "All",
        playtest_filter: PlaytestFilter = "All",
    ) -> None:
        """Search for maps with filters such as name, code, difficulty, quality, and more.

        Runs a query against the API using the provided filters and displays the
        results in a paginated view. Supports filtering by restrictions, mechanics,
        playtest/medal/completion state, and category.

        Args:
            itx: The command interaction context.
            map_name: Optional map name filter..
            difficulty: Optional exact difficulty choice.
            code: Optional map code filter.
            creator: Optional creator user ID.
            mechanic: Optional mechanic filter.
            restriction: Optional restriction filter.
            minimum_quality: Optional minimum star rating filter.
            category: Optional category filter.
            official_filter: Optional official map filter. Defaults to "Official Only".
            completion_filter: Filter maps by completion state. Defaults to "All".
            medal_filter: Filter maps by medal availability. Defaults to "All".
            playtest_filter: Filter maps by playtest state. Defaults to "All".
        """
        await itx.response.defer(ephemeral=True)
        restrictions: list[Restrictions] | None = [restriction] if restriction else None
        mechanics: list[Mechanics] | None = [mechanic] if mechanic else None
        if official_filter == "All":
            official_val = None
        elif official_filter == "Official Only":
            official_val = True
        else:
            official_val = False
        if code:
            maps = [await self.bot.api.get_map(code=code)]
        else:
            maps = await self.bot.api.get_maps(
                map_name=[map_name] if map_name else None,
                official=official_val,
                restrictions=restrictions,
                mechanics=mechanics,
                difficulty_exact=cast("DifficultyTop", difficulty.value) if difficulty else None,
                minimum_quality=minimum_quality.value if minimum_quality else None,
                creator_ids=[creator] if creator else None,
                playtest_filter=playtest_filter,
                medal_filter=medal_filter,
                completion_filter=completion_filter,
                category=[category] if category else None,
                return_all=True,
            )
        view = MapSearchView(maps)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @app_commands.command(name="view-guides")
    async def view_guides(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeVisibleTransformer],
        include_records: bool = False,
    ) -> None:
        """View guides for a particular map code.

        Args:
            itx (GenjiItx): Discord interaction.
            code (OverwatchCode): The map code.
            include_records (bool): Whether to include submitted records in the search.
        """
        await itx.response.defer(ephemeral=True)
        guides = await self.bot.api.get_guides(code, include_records)
        for guide in guides:
            guide.thumbnail = await self.bot.thumbnail_service.get_thumbnail(guide.url)
        view = MapGuideView(code, guides)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx


async def setup(bot: Genji) -> None:
    """Load the MapSearchCog cog."""
    await bot.add_cog(MapSearchCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the MapSearchCog cog."""
    await bot.remove_cog("MapSearchCog")
