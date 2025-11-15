from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Literal

import discord
from discord import TextChannel, app_commands, ui
from discord.ext import commands
from genjipk_sdk.models import PlaytestPatchDTO

from extensions.playtest import MapFinalizationViewV2, PlaytestCog
from utilities.base import BaseCog
from utilities.errors import UserFacingError

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiCtx, GenjiItx

log = getLogger(__name__)


class HousekeepingCog(BaseCog):
    def __init__(self, bot: Genji) -> None:
        """Initialize the HousekeepingCog."""
        super().__init__(bot)
        self.repair_context_menu = app_commands.ContextMenu(
            name="Repair View",
            callback=self.repair,
        )
        self.bot.tree.add_command(self.repair_context_menu)

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def test(
        self,
        ctx: GenjiCtx,
    ) -> None:
        """Test command."""

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def cleanuppt(
        self,
        ctx: GenjiCtx,
    ) -> None:
        """Test command."""
        assert ctx.guild
        channel = ctx.guild.get_channel(self.bot.config.channels.submission.playtest)
        assert isinstance(channel, discord.ForumChannel)
        for thread in channel.threads:
            await thread.delete()

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def purge(
        self,
        ctx: GenjiCtx,
        limit: int,
    ) -> None:
        """Test command."""
        assert isinstance(ctx.channel, TextChannel)
        await ctx.channel.purge(limit=limit)
        await ctx.send("Purged.", delete_after=2)

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync(
        self,
        ctx: GenjiCtx,
        guilds: commands.Greedy[discord.Object],
        spec: Literal["~", "*", "^", "$"] | None = None,
    ) -> None:
        """Sync commands to Discord.

        ?sync -> global sync
        ?sync ~ -> sync current guild
        ?sync * -> copies all global app commands to the current guild and syncs
        ?sync ^ -> clears all commands from the current
                        guild target and syncs (removes guild commands)
        ?sync id_1 id_2 -> syncs guilds with id 1 and 2
        >sync $ -> Clears global commands
        """
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                assert ctx.guild
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            elif spec == "$":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync()
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}")
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    async def repair(self, itx: GenjiItx, message: discord.Message) -> None:
        """Repair broken views."""
        await itx.response.defer(ephemeral=True, thinking=True)
        if message.channel.id == self.bot.config.channels.submission.verification_queue:
            saved_view = self.bot.completions.verification_views.get(message.id, None)
            if not saved_view:
                raise UserFacingError("Looks like there is no view associated with this message.")
            for c in saved_view.walk_children():
                if isinstance(c, ui.Button):
                    c.disabled = False
            await message.edit(view=saved_view)

        if message.channel.id == itx.client.config.channels.submission.playtest:
            cog: "PlaytestCog" = self.bot.get_cog("PlaytestCog")  # pyright: ignore[reportAssignmentType]
            if _view := cog.verification_views.get(message.id):
                await message.edit(view=saved_view)

        await itx.edit_original_response(content="The view has been repaired.")

    @commands.command()
    async def playtest_fix(self, ctx: GenjiCtx) -> None:
        p_ids = (
            (142, "001XK", None),
            (34, "0D6R3", None),
            (176, "101MY", None),
            (15, "10PKG", None),
            (152, "162T6", None),
            (197, "1MZDK", 1438580849372168315),
            (25, "1PAYAW", None),
            (200, "2R5M4", 1438625395757944934),
            (159, "30RJ0", 1426168025421451304),
            (149, "38603", None),
            (20, "4CDTA", None),
            (171, "4W42N", None),
            (154, "68VKC", 1438455381109112895),
            (11, "6CDCW", None),
            (111, "6RG452", None),
            (22, "86764", None),
            (153, "8914R", None),
            (190, "8J0XW", None),
            (3, "8NYFP", None),
            (29, "8ZYPQ", None),
            (198, "9V7JN", None),
            (156, "A8XZZ", None),
            (150, "CE0M0", None),
            (165, "CQ7RCZ", None),
            (24, "CS0ZE", None),
            (16, "D06DH", None),
            (130, "D851B", None),
            (115, "D9DDT", None),
            (109, "DHT31", None),
            (23, "DP0CJ", None),
            (108, "EE0N5", None),
            (13, "FZD4W", None),
            (19, "G8NC1", None),
            (27, "G93VC", None),
            (14, "GTD5P", None),
            (199, "GZ67K", 1438127915375263787),
            (166, "KRTZE", None),
            (132, "KT9CK", None),
            (28, "KTKCF", None),
            (140, "MNW3N", None),
            (18, "MTBBA", None),
            (169, "NNK66", None),
            (145, "P1SVV", None),
            (31, "P2QPJ", None),
            (134, "Q1S0M", None),
            (118, "Q5XMH", None),
            (12, "Q87FR", None),
            (26, "QS6EB", None),
            (21, "RW93E", None),
            (6, "S9GHV", None),
            (10, "SBSN4", None),
            (189, "SEZAW", None),
            (7, "SJ08K", None),
            (146, "VFAWB", None),
            (119, "VH286", None),
            (9, "VR9WE", None),
            (8, "WMDAN", None),
            (2, "XBVJT", None),
            (17, "XPRE4", None),
            (30, "SB4V8", None),
        )

        for p_id, code, v_id in p_ids:
            model = await self.bot.api.get_partial_map(code)
            await ctx.bot.playtest._add_playtest(model, p_id)
            if v_id:
                assert ctx.guild
                verification_channel = ctx.guild.get_channel(ctx.bot.config.channels.submission.verification_queue)
                assert isinstance(verification_channel, discord.TextChannel)
                data = await ctx.bot.api.get_map(code=code)
                view = MapFinalizationViewV2(ctx.guild.id, data)
                verification_message = await verification_channel.send(view=view)
                playtest_edit_data = PlaytestPatchDTO(verification_id=verification_message.id)
                if not data.playtest:
                    raise AttributeError("The data is missing playtest.")
                await ctx.bot.api.edit_playtest_meta(thread_id=data.playtest.thread_id, data=playtest_edit_data)


async def setup(bot: Genji) -> None:
    """Load the HousekeepingCog cog."""
    await bot.add_cog(HousekeepingCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the HousekeepingCog cog."""
    await bot.remove_cog("HousekeepingCog")
