from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Literal

import discord
from discord import TextChannel, app_commands, ui
from discord.ext import commands

from extensions.playtest import PlaytestCog
from utilities.base import BaseCog

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
        cog: "PlaytestCog" = self.bot.get_cog("PlaytestCog")  # pyright: ignore[reportAssignmentType]
        if message.channel.id == self.bot.config.channels.submission.verification_queue:
            saved_view = self.bot.completions.verification_views.get(message.id, None)
            if saved_view:
                for c in saved_view.walk_children():
                    if isinstance(c, ui.Button):
                        c.disabled = False
                await message.edit(view=saved_view)
            elif _view := cog.verification_views.get(message.id):
                await message.edit(view=_view)

        elif (
            isinstance(message.thread, discord.Thread)
            and message.thread.parent
            and message.thread.parent.id == self.bot.config.channels.submission.playtest
        ):
            if _view := cog.playtest_views.get(message.id):
                await message.edit(view=_view)

        await itx.edit_original_response(content="The view has been repaired.")


async def setup(bot: Genji) -> None:
    """Load the HousekeepingCog cog."""
    await bot.add_cog(HousekeepingCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the HousekeepingCog cog."""
    await bot.remove_cog("HousekeepingCog")
