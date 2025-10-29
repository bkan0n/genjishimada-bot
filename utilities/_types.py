from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from discord.ext import commands

    import core

    GenjiItx = discord.Interaction[core.Genji]
    GenjiCtx = commands.Context[core.Genji]
