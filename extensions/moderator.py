from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from aiohttp.client import ClientResponseError
from discord import Member, app_commands
from genjipk_sdk.models import MapPatchDTO, Medals, QualityValueDTO, SendToPlaytestDTO
from genjipk_sdk.models.maps import LinkMapsCreateDTO, UnlinkMapsCreateDTO
from genjipk_sdk.utilities import DifficultyAll
from genjipk_sdk.utilities._types import (
    MapCategory,
    Mechanics,
    OverwatchCode,
    OverwatchMap,
    PlaytestStatus,
    Restrictions,
)
from msgspec import UNSET

from utilities import transformers
from utilities.base import BaseCog, ConfirmationView
from utilities.errors import UserFacingError
from utilities.views.mod_creator_view import MapCreatorModView
from utilities.views.mod_edit_map_views import MechanicsEditView, RestrictionsEditView
from utilities.views.mod_guides_view import ModGuidePaginatorView
from utilities.views.mod_status_view import ModStatusView

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx

log = getLogger(__name__)


async def edit_map_field(
    itx: GenjiItx,
    code: OverwatchCode,
    field_name: str,
    new_value: Any,  # noqa: ANN401
    formatter: Callable[[Any], str] = str,
) -> None:
    """Generic handler to edit a single field on a map.

    Args:
        itx (GenjiItx): The interaction context.
        code (OverwatchCode): The Overwatch code identifying the map.
        field_name (str): The field name to update on the map.
        new_value (Any): The new value to set for the field.
        formatter (Callable[[Any], str], optional): Function to format the confirmation message. Defaults to `str`.

    Raises:
        ValueError: If the map could not be retrieved.
    """
    await itx.response.defer(ephemeral=True)

    res = await itx.client.api.get_map(code=code)
    if not res:
        raise ValueError("This shouldn't happen.")

    old_value = getattr(res, field_name)
    message = (
        f"Are you sure you want to change the {field_name} for this map?\n"
        f"Old: {formatter(old_value)}\nNew: {formatter(new_value)}"
    )

    async def callback() -> None:
        await itx.client.api.edit_map(code, MapPatchDTO(**{field_name: new_value}))

    view = ConfirmationView(message, callback)
    await itx.edit_original_response(view=view)
    view.original_interaction = itx


class ModeratorCog(BaseCog):
    mod = app_commands.Group(
        name="mod",
        description="Mod only commands",
    )
    map = app_commands.Group(
        name="map",
        description="Mod only commands",
        parent=mod,
    )
    record = app_commands.Group(
        name="edit-record",
        description="Mod only commands",
        parent=mod,
    )
    user = app_commands.Group(
        name="edit-user",
        description="Mod only commands",
        parent=mod,
    )

    @map.command(name="edit-guides")
    async def edit_delete_guides(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Open guide removal interface for a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to modify.
        """
        await itx.response.defer(ephemeral=True)
        guides = await itx.client.api.get_guides(code)
        view = ModGuidePaginatorView(code, guides, itx.client)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @map.command(name="edit-creators")
    async def edit_delete_creators(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Open creator editing interface for a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to modify.
        """
        await itx.response.defer(ephemeral=True)
        data = await itx.client.api.get_map(code=code)
        view = MapCreatorModView(data)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @map.command(name="edit-code")
    async def edit_code(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        new_code: app_commands.Transform[OverwatchCode, transformers.CodeSubmissionTransformer],
    ) -> None:
        """Edit the code assigned to a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The original code.
            new_code (OverwatchCode): The new code to assign.
        """
        await edit_map_field(itx, code, "code", new_code)

    @map.command(name="edit-name")
    async def edit_name(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        map_name: app_commands.Transform[OverwatchMap, transformers.MapNameTransformer],
    ) -> None:
        """Edit the display name of a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            map_name (OverwatchMap): The new name.
        """
        await edit_map_field(itx, code, "map_name", map_name)

    @map.command(name="edit-category")
    async def edit_category(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        category: MapCategory,
    ) -> None:
        """Edit the category of a map (e.g., Puzzle, Parkour).

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            category (MapCategory): The new category.
        """
        await edit_map_field(itx, code, "category", category)

    @map.command(name="edit-checkpoints")
    async def edit_checkpoints(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        checkpoints: app_commands.Range[int, 2, None],
    ) -> None:
        """Edit the number of checkpoints for a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            checkpoints (int): New checkpoint count (must be ≥ 2).
        """
        await edit_map_field(itx, code, "checkpoints", checkpoints)

    @map.command(name="edit-status")
    async def edit_status(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Open interface to edit the verification and playtesting status of a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
        """
        await itx.response.defer(ephemeral=True)
        data = await itx.client.api.get_map(code=code)
        view = ModStatusView(data)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx
        await view.wait()
        if not view.confirmed:
            return

        playtesting = (
            cast("PlaytestStatus", view.playtest_status_select.values[0])
            if view.playtest_status_select.values
            else UNSET
        )

        await self.bot.api.edit_map(
            code,
            MapPatchDTO(
                hidden=view.hidden_button.enabled,
                official=view.official_button.enabled,
                archived=view.archived_button.enabled,
                playtesting=playtesting,
            ),
        )
        if view.send_to_playtest_button.enabled:
            playtesting_difficulty = cast(DifficultyAll, view.playtest_difficulty_select.values[0])
            await self.bot.api.send_map_to_playtest(data.code, SendToPlaytestDTO(playtesting_difficulty))

    @map.command(name="edit-difficulty")
    async def edit_difficulty(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        difficulty: DifficultyAll,
    ) -> None:
        """Edit the difficulty label of a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            difficulty (DifficultyAll): New difficulty value.
        """
        await edit_map_field(itx, code, "difficulty", difficulty)

    @map.command(name="edit-description")
    async def edit_description(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        description: str,
    ) -> None:
        """Edit the public description of a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            description (str): New description.
        """
        await edit_map_field(itx, code, "description", description)

    @map.command(name="edit-title")
    async def edit_title(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        title: str,
    ) -> None:
        """Edit the short-form title displayed in embeds or listings.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            title (str): New title.
        """
        await edit_map_field(itx, code, "title", title)

    @map.command(name="edit-map-banner")
    async def edit_banner(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        banner_url: str,
    ) -> None:
        """Edit the map banner image URL.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            banner_url (str): URL of the new banner image.
        """
        await edit_map_field(itx, code, "map_banner", banner_url)

    @map.command(name="edit-medals")
    async def edit_medals(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        gold: app_commands.Transform[float, transformers.RecordTransformer] | None,
        silver: app_commands.Transform[float, transformers.RecordTransformer] | None,
        bronze: app_commands.Transform[float, transformers.RecordTransformer] | None,
    ) -> None:
        """Edit the medal thresholds (gold/silver/bronze) for a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code.
            gold (float | None): Optional new gold time.
            silver (float | None): Optional new silver time.
            bronze (float | None): Optional new bronze time.

        Raises:
            UserFacingError: If no values are provided or if partial edits are attempted when no medals exist.
            ValueError: If the map could not be retrieved.
        """
        if not gold and not silver and not bronze:
            raise UserFacingError("You need to edit at least one medal.")
        await itx.response.defer(ephemeral=True)

        res = await itx.client.api.get_map(code=code)
        if not res:
            raise UserFacingError(f"The map code entered (`{code}`) does not exist.")

        if (any((gold, silver, bronze)) and not all((gold, silver, bronze))) and res.medals is None:
            raise UserFacingError(
                "This map currently has no medals. You must set all medals before partially editing medals."
            )
        old_gold, old_silver, old_bronze = (
            (res.medals.gold, res.medals.silver, res.medals.bronze) if res.medals else (None, None, None)
        )

        medal_changes = []
        if gold is not None:
            medal_changes.append(f"Gold: {old_gold} → {gold}")
        if silver is not None:
            medal_changes.append(f"Silver: {old_silver} → {silver}")
        if bronze is not None:
            medal_changes.append(f"Bronze: {old_bronze} → {bronze}")

        message = "Are you sure you want to change the medal thresholds for this map?\n" + "\n".join(medal_changes)

        async def callback() -> None:
            medals = Medals(
                gold=gold if gold is not None else res.medals.gold,  # type: ignore
                silver=silver if silver is not None else res.medals.silver,  # type: ignore
                bronze=bronze if bronze is not None else res.medals.bronze,  # type: ignore
            )
            await itx.client.api.edit_map(code, MapPatchDTO(medals=medals))

        view = ConfirmationView(message, callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @map.command(name="edit-mechanics")
    async def edit_mechanics(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Edit the mechanics for a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to modify.

        Raises:
            UserFacingError: If the map could not be retrieved.
        """
        await itx.response.defer(ephemeral=True)
        map_data = await itx.client.api.get_map(code=code)
        view = MechanicsEditView(code, defaults=map_data.mechanics)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx
        await view.wait()
        if set(view.select.values) == set(map_data.mechanics):
            return
        await itx.client.api.edit_map(code, MapPatchDTO(mechanics=cast("list[Mechanics]", view.select.values)))

    @map.command(name="edit-restrictions")
    async def edit_restrictions(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Edit the restrictions for a specific map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to modify.

        Raises:
            UserFacingError: If the map could not be retrieved.
        """
        await itx.response.defer(ephemeral=True)
        map_data = await itx.client.api.get_map(code=code)
        if not map_data:
            raise UserFacingError(f"The map code entered (`{code}`) does not exist.")
        view = RestrictionsEditView(code, defaults=map_data.restrictions)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx
        await view.wait()
        if set(view.select.values) == set(map_data.mechanics):
            return
        await itx.client.api.edit_map(code, MapPatchDTO(restrictions=cast("list[Restrictions]", view.select.values)))

    @map.command(name="link-codes")
    async def link_codes(
        self,
        itx: GenjiItx,
        official_code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        unofficial_code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Link an official and unofficial map.

        Args:
            itx (GenjiItx): The interaction context.
            official_code (OverwatchCode): The official map code to link.
            unofficial_code (OverwatchCode): The unofficial map code to link.

        Raises:
            UserFacingError: If the map could not be retrieved.
        """
        data = LinkMapsCreateDTO(official_code=official_code, unofficial_code=unofficial_code)

        message = (
            "Are you sure you want to link these two maps?\n"
            f"`Official` {official_code}\n"
            f"`Unofficial (CN)` {unofficial_code}\n"
        )

        async def callback() -> None:
            await itx.client.api.link_map_codes(data)

        view = ConfirmationView(message, callback)
        await itx.response.send_message(view=view)
        view.original_interaction = itx

    @map.command(name="unlink-codes")
    async def unlink_codes(
        self,
        itx: GenjiItx,
        official_code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        unofficial_code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        reason: str,
    ) -> None:
        """Unlink an official and unofficial map.

        Args:
            itx (GenjiItx): The interaction context.
            official_code (OverwatchCode): The official map code to unlink.
            unofficial_code (OverwatchCode): The unofficial map code to unlink.

        """
        data = UnlinkMapsCreateDTO(official_code=official_code, unofficial_code=unofficial_code, reason=reason)

        message = (
            "Are you sure you want to unlink these two maps?\n"
            f"`Official` {official_code}\n"
            f"`Unofficial (CN)` {unofficial_code}\n"
        )

        async def callback() -> None:
            try:
                await itx.client.api.unlink_map_codes(data)
            except ClientResponseError as e:
                log.info(dir(e))
                raise UserFacingError(e.message)

        view = ConfirmationView(message, callback)
        await itx.response.send_message(view=view)
        view.original_interaction = itx

    @record.command(name="convert-legacy")
    async def convert_legacy(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
    ) -> None:
        """Mark existing records as legacy for a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to convert.

        Raises:
            UserFacingError: If the map has no legacy records to convert.
        """
        await itx.response.defer(ephemeral=True)

        res = await itx.client.api.get_map(code=code)
        if not res:
            raise UserFacingError(f"The map code entered (`{code}`) does not exist.")

        message = "Are you sure you want to convert all completions for this map to legacy? This cannot be undone."

        async def callback() -> None:
            await itx.client.api.convert_map_to_legacy(code)

        view = ConfirmationView(message, callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @map.command(name="edit-rating")
    async def edit_rating(
        self,
        itx: GenjiItx,
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer],
        value: app_commands.Range[int, 1, 6],
    ) -> None:
        """Edit the quality rating of a map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode): The map code to update.
            value (int): An integer between 1 and 6 (inclusive).

        Raises:
            UserFacingError: If the new rating is invalid or unsupported.
        """
        await itx.response.defer(ephemeral=True)

        res = await itx.client.api.get_map(code=code)
        if not res:
            raise UserFacingError(f"The map code entered (`{code}`) does not exist.")

        message = f"Are you sure you want to override the quality value for this map (`{code}`)? This cannot be undone."

        async def callback() -> None:
            await itx.client.api.override_quality_votes(code, QualityValueDTO(value=value))

        view = ConfirmationView(message, callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @user.command(name="create-fake-user")
    async def create_fake_user(self, itx: GenjiItx, name: str) -> None:
        """Create a 'fake' user for submissions.

        This user is not linked to a real Discord account and can be used in test completions or moderation workflows.

        Args:
            itx (GenjiItx): The interaction context.
            name (str): The name of the fake user.

        Raises:
            UserFacingError: If a fake user could not be created.
        """
        await itx.response.defer(ephemeral=True)

        message = f"Are you sure you want to create a fake user with the name: `{name}`?"

        async def callback() -> None:
            await itx.client.api.create_fake_member(name)

        view = ConfirmationView(message, callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @user.command(name="link-fake-user")
    async def link_fake_user_to_real(
        self,
        itx: GenjiItx,
        fake_member: app_commands.Transform[int, transformers.FakeUserTransformer],
        real_member: Member,
    ) -> None:
        """Link a previously created fake user to a real user.

        Transfers any data (completions, verifications, etc.) from a fake user to an actual user account.

        Args:
            itx (GenjiItx): The interaction context.
            fake_member (int): Transformed autocomplete user into an user_id
            real_member (Member): The real member to link.

        Raises:
            UserFacingError: If the user IDs are invalid or incompatible.
        """
        await itx.response.defer(ephemeral=True)

        fake_member_data = await self.bot.api.get_user(fake_member)
        if not fake_member_data:
            raise UserFacingError("Fake user was not found.")

        real_member_data = await self.bot.api.get_user(real_member.id)
        if not real_member_data:
            raise UserFacingError("Real user was not found.")
        message = (
            "Are you sure you want to link these members?\n\n"
            f"{fake_member_data.coalesced_name} ({fake_member_data.id}) data will be merged with "
            f"{real_member_data.coalesced_name} ({real_member_data.id})\n"
            f"{fake_member_data.coalesced_name} ({fake_member_data.id}) will be removed after this is confirmed. "
            "This cannot be undone."
        )

        async def callback() -> None:
            await itx.client.api.link_fake_member_id_to_real_user_id(fake_member_data.id, real_member_data.id)

        view = ConfirmationView(message, callback)
        await itx.edit_original_response(view=view)
        view.original_interaction = itx

    @record.command(name="manage")
    async def manage_records(
        self,
        itx: GenjiItx,
        # optional code <- lists all records for code or optionally filtered by a single user
        code: app_commands.Transform[OverwatchCode, transformers.CodeAllTransformer] | None,
        # optional user <- lists all records for user or optionally filtered by a single code
        user: app_commands.Transform[int, transformers.UserTransformer] | None,
        verification_status: Literal["Unverified", "Verified", "All"] = "All",
        latest_only: bool = True,
    ) -> None:
        """Manage records for a given user or map.

        Args:
            itx (GenjiItx): The interaction context.
            code (OverwatchCode | None): Optional map code to filter records.
            user (int | None): Optional user ID to filter records.
            verification_status (Literal["Unverified", "Verified", "All"]): Filter records by verification status.
            latest_only (bool): Whether to only show the most recent run per user.
        """


async def setup(bot: Genji) -> None:
    """Load the ModeratorCog cog.

    Args:
        bot (Genji): The bot instance.
    """
    await bot.add_cog(ModeratorCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the ModeratorCog cog.

    Args:
        bot (Genji): The bot instance.
    """
    await bot.remove_cog("ModeratorCog")
