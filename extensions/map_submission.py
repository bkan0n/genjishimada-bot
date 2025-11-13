from __future__ import annotations

from typing import TYPE_CHECKING

from discord import Attachment, app_commands
from genjipk_sdk.models import Guide
from genjipk_sdk.utilities._types import (
    MapCategory,
    OverwatchCode,
    OverwatchMap,
)

from utilities import transformers
from utilities.base import BaseCog, ConfirmationView
from utilities.maps import MapSubmissionView, PartialMapCreateModel

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx


class MapSubmissionCog(BaseCog):
    @app_commands.command(name="submit-map")
    async def submit_map(  # noqa: PLR0913
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeSubmissionTransformer],
        map_name: app_commands.Transform[OverwatchMap, transformers.MapNameTransformer],
        checkpoints: app_commands.Range[int, 2, None],
        category: MapCategory,
        description: str | None = None,
        guide_url: str | None = None,
        custom_title: app_commands.Range[str, 1, 100] | None = None,
        custom_banner: Attachment | None = None,
        gold: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
        silver: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
        bronze: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
    ) -> None:
        """Begin the map submission process.

        Allows a user to submit a map for verification or leaderboard inclusion. Opens
        an interactive form with the provided metadata for review and completion.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map's unique code (e.g., A1B2C3).
            map_name (OverwatchMap): The official map name (e.g., Temple of Anubis).
            checkpoints (int): The number of checkpoints in the map (minimum 2).
            category (MapCategory): The category to classify the map under.
            description (str, optional): Optional short description of the map.
            guide_url (str, optional): Optional URL to a video or written guide.
            custom_title (str, optional): Optional custom display title.
            custom_banner (Attachment, optional): Optional custom banner image.
            gold (float | None, optional): Optional gold medal time threshold.
            silver (float | None, optional): Optional silver medal time threshold.
            bronze (float | None, optional): Optional bronze medal time threshold.

        Returns:
            None

        Raises:
            UserFacingError: If submission validation fails.
        """
        await itx.response.defer(ephemeral=True)

        custom_banner_url = None
        if custom_banner:
            image = await custom_banner.read()
            custom_banner_url = await self.bot.api.upload_image(
                image, content_type=custom_banner.content_type or "image/png"
            )

        partial = PartialMapCreateModel(
            code,
            map_name,
            checkpoints,
            category,
            itx.user.id,
            itx.user.name,
            gold,
            silver,
            bronze,
            description,
            guide_url,
            custom_title,
            custom_banner_url,
        )
        view = MapSubmissionView(partial)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @app_commands.command(name="submit-guide")
    async def submit_guide(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeVisibleTransformer],
        url: str,
    ) -> None:
        """Submit a guide."""
        thumbnail = await self.bot.thumbnail_service.get_thumbnail(url)

        view = ConfirmationView(f"# Does this look correct?\n`Code` {code}\n`URL` {url}", image_url=thumbnail)
        await itx.response.send_message(view=view, ephemeral=True)
        view.original_interaction = itx
        await view.wait()
        if not view.confirmed:
            return
        data = Guide(url, itx.user.id)
        await self.bot.api.create_guide(code, data)

    # @app_commands.command(name="")
    # @app_commands.rename(
    #     code="",
    #     map_name="",
    #     checkpoints="",
    #     category="",
    #     description="",
    #     guide_url="",
    #     custom_title="",
    #     custom_banner="",
    #     gold="",
    #     silver="",
    #     bronze="",
    # )
    # async def submit_map_cn(
    #     self,
    #     itx: GenjiItx,
    #     code: app_commands.Transform[OverwatchCode, transformers.CodeSubmissionTransformer],
    #     map_name: app_commands.Transform[OverwatchMap, transformers.MapNameTransformer],
    #     checkpoints: app_commands.Range[int, 2, None],
    #     category: MapCategory,
    #     description: str | None = None,
    #     guide_url: str | None = None,
    #     custom_title: app_commands.Range[str, 1, 100] | None = None,
    #     custom_banner: Attachment | None = None,
    #     gold: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
    #     silver: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
    #     bronze: app_commands.Transform[float, transformers.RecordTransformer] | None = None,
    # ) -> None:
    #     """Begin the map submission process.

    #     Allows a user to submit a map for verification or leaderboard inclusion. Opens
    #     an interactive form with the provided metadata for review and completion.

    #     Args:
    #         itx (GenjiItx): The interaction context.
    #         code (OverwatchCode): The map's unique code (e.g., A1B2C3).
    #         map_name (OverwatchMap): The official map name (e.g., Temple of Anubis).
    #         checkpoints (int): The number of checkpoints in the map (minimum 2).
    #         category (MapCategory): The category to classify the map under.
    #         description (str, optional): Optional short description of the map.
    #         guide_url (str, optional): Optional URL to a video or written guide.
    #         custom_title (str, optional): Optional custom display title.
    #         custom_banner (Attachment, optional): Optional custom banner image.
    #         gold (float | None, optional): Optional gold medal time threshold.
    #         silver (float | None, optional): Optional silver medal time threshold.
    #         bronze (float | None, optional): Optional bronze medal time threshold.

    #     Returns:
    #         None

    #     Raises:
    #         UserFacingError: If submission validation fails.
    #     """
    #     await itx.response.defer(ephemeral=True)

    #     custom_banner_url = None
    #     if custom_banner:
    #         image = await custom_banner.read()
    #         custom_banner_url = await self.bot.api.upload_image(
    #             image, content_type=custom_banner.content_type or "image/png"
    #         )

    #     partial = PartialMapCreateModel(
    #         code,
    #         map_name,
    #         checkpoints,
    #         category,
    #         itx.user.id,
    #         itx.user.name,
    #         gold,
    #         silver,
    #         bronze,
    #         description,
    #         guide_url,
    #         custom_title,
    #         custom_banner_url,
    #     )
    #     view = MapSubmissionView(partial)
    #     await itx.edit_original_response(view=view)
    #     view.original_interaction = itx


async def setup(bot: Genji) -> None:
    """Load the MapSubmissionCog cog."""
    await bot.add_cog(MapSubmissionCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the MapSubmissionCog cog."""
    await bot.remove_cog("MapSubmissionCog")
