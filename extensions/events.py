from __future__ import annotations

import datetime as dt
from logging import getLogger
from typing import TYPE_CHECKING

import discord
from discord import Member, User
from discord.app_commands import Command, ContextMenu
from discord.ext import commands, tasks
from genjipk_sdk.models import NewsfeedAnnouncement, NewsfeedEvent, UserCreateDTO, UserUpdateDTO

from utilities.base import BaseCog

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiItx


log = getLogger(__name__)


class EventsCog(BaseCog):
    def __init__(self, bot: Genji) -> None:
        """Initialize the EventsCog.

        Starts the ninja_check task.
        """
        super().__init__(bot)
        self.ninja_check.start()

    async def cog_unload(self) -> None:
        """Stop tasks for running upon cog unload."""
        self.ninja_check.cancel()
        await super().cog_unload()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for messages in the Announcement channel for newsfeed scraping."""
        if message.channel.id != self.bot.config.channels.updates.announcements:
            return
        if message.author.bot:
            return

        data = NewsfeedAnnouncement(
            title="Announcement",
            content=message.content,
            url=None,
            banner_url=None if not message.attachments else message.attachments[0].url,
            thumbnail_url=None,
            from_discord=True,
        )
        event = NewsfeedEvent(
            id=None, timestamp=dt.datetime.now(dt.timezone.utc), payload=data, event_type="announcement"
        )
        await self.bot.api.create_newsfeed(event)

    @commands.Cog.listener()
    async def on_member_join(self, member: Member) -> None:
        """Handle a new member joining the guild.

        Creates a user record in the API based on the member's Discord info.

        Args:
            member (Member): The newly joined member.
        """
        if not self.bot.api.check_user_exists(member.id):
            data = UserCreateDTO(
                member.id,
                member.global_name or member.name,
                member.nick or member.name,
            )
            await self.bot.api.create_user(data)
        ninja_role = discord.utils.get(member.guild.roles, name="Ninja")
        assert ninja_role
        await member.add_roles(ninja_role)
        if self.bot.api.check_user_is_creator(member.id):
            creator_role = discord.utils.get(member.guild.roles, name="Map Creator")
            assert creator_role
            await member.add_roles(creator_role)

        await self.bot.completions.auto_skill_role(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: Member, after: Member) -> None:
        """Handle updates to a guild member (e.g., nick/roles).

        Args:
            before (Member): Member state before the update.
            after (Member): Member state after the update.
        """
        if before.nick != after.nick and after.nick is not None:
            data = UserUpdateDTO(nickname=after.nick)
            await self.bot.api.update_user_names(after.id, data)

    @commands.Cog.listener()
    async def on_user_update(self, before: User, after: User) -> None:
        """Handle global user profile updates (e.g., username, avatar).

        Args:
            before (User): User state before the update.
            after (User): User state after the update.
        """
        if before.global_name != after.global_name and after.global_name is not None:
            data = UserUpdateDTO(global_name=after.global_name)
            await self.bot.api.update_user_names(after.id, data)

    @tasks.loop(minutes=1)
    async def ninja_check(self) -> None:
        """Create a loop that checks all users for the Ninja role."""
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(self.bot.config.guild)
        if not guild:
            return
        ninja_role = discord.utils.get(guild.roles, name="Ninja")
        assert ninja_role

        for member in guild.members:
            if not member.get_role(ninja_role.id):
                await member.add_roles(ninja_role)

    @commands.Cog.listener()
    async def on_interaction(self, itx: GenjiItx) -> None:
        """Intercept interaction for logging."""
        if not itx.command:
            return

        if itx.type != discord.InteractionType.application_command:
            return

        _namespace = {}
        for k, v in itx.namespace.__dict__.items():
            if isinstance(v, (str, int, float)):
                _namespace[k] = v
            elif isinstance(v, (discord.Member, discord.User, discord.Role, discord.Guild)):
                _namespace[k] = str(v)

        log.debug(
            "On Interaction Logging the following info\n\n %s %s %s %s",
            itx.command.qualified_name,
            itx.user.id,
            itx.created_at,
            _namespace,
        )
        await self.bot.api.log_analytics(itx.command.qualified_name, itx.user.id, itx.created_at, _namespace)

    @commands.Cog.listener()
    async def on_app_command_completion(self, itx: GenjiItx, command: Command | ContextMenu): ...


async def setup(bot: Genji) -> None:
    """Load the EventsCog.

    Args:
        bot (Genji): Bot instance to extend.
    """
    await bot.add_cog(EventsCog(bot))


async def teardown(bot: Genji) -> None:
    """Unload the EventsCog.

    Args:
        bot (Genji): Bot instance to modify.
    """
    await bot.remove_cog("EventsCog")
