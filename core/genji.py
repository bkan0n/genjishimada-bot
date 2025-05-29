import logging

import discord
from discord.ext import commands

__all__ = ("Genji",)

log = logging.getLogger(__name__)

intents = discord.Intents(
    guild_messages=True,
    guilds=True,
    integrations=True,
    dm_messages=True,
    webhooks=True,
    members=True,
    message_content=True,
    guild_reactions=True,
)


class Genji(commands.Bot):
    def __init__(self, *, prefix: str) -> None:
        super().__init__(
            command_prefix=prefix,
            intents=intents,
            help_command=None,
            description="Genji Shimada, a Discord bot for the Genji Parkour community.",
        )

    async def on_ready(self) -> None:
        """Log when the bot is ready."""
        log.info(f"Logged in as {self.user}")
