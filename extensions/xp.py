from __future__ import annotations

from logging import getLogger
from math import floor
from typing import TYPE_CHECKING

import msgspec
from aio_pika.abc import AbstractIncomingMessage
from discord import TextChannel, app_commands, utils
from discord.ext import commands
from genjipk_sdk.users import Notification
from genjipk_sdk.xp import XP_AMOUNTS, XP_TYPES, XpGrantEvent, XpGrantRequest

from extensions._queue_registry import register_queue_handler
from utilities import transformers
from utilities.base import BaseService

if TYPE_CHECKING:
    import core
    from utilities._types import GenjiItx

log = getLogger(__name__)


# TODO: Make alerts into cv2, pretty
class XPService(BaseService):
    xp_channel: TextChannel

    async def _resolve_channels(self) -> None:
        """Resolve and cache channels used by the XP system.

        Asserts that the configured XP channel exists and stores it on the
        instance for later use.
        """
        xp_channel = self.bot.get_channel(self.bot.config.channels.updates.xp)
        assert isinstance(xp_channel, TextChannel)
        self.xp_channel = xp_channel

    async def _process_xp_notification(self, xp_message: XpGrantEvent) -> None:
        """Send XP gain notifications and handle rank/prestige side effects.

        Posts a public XP gain notice (respecting user notification settings),
        checks for rank and prestige changes via the API, grants lootbox keys,
        updates Discord roles, and sends appropriate DMs and channel messages.

        Args:
            xp_message (XpGrantMQ): Result payload containing data for notifications.
        """
        user = self.guild.get_member(xp_message.user_id)
        if not user:
            return

        multiplier = await self.bot.api.get_xp_multiplier()
        amount = floor(xp_message.amount * multiplier)

        await self.bot.notifications.notify_channel_default_to_no_ping(
            self.xp_channel,
            xp_message.user_id,
            Notification.PING_ON_XP_GAIN,
            f"<:_:976917981009440798> {user.display_name} has gained **{amount} XP** ({xp_message.type})!",
        )

        xp_data = await self.bot.api.get_xp_tier_change(xp_message.previous_amount, xp_message.new_amount)

        if xp_data.rank_change_type:
            old_rank = " ".join((xp_data.old_main_tier_name, xp_data.old_sub_tier_name))
            new_rank = " ".join((xp_data.new_main_tier_name, xp_data.new_sub_tier_name))

            await self.bot.api.grant_active_key_to_user(xp_message.user_id)
            await self._update_xp_roles_for_user(
                xp_message.user_id, xp_data.old_main_tier_name, xp_data.new_main_tier_name
            )

            await self.bot.notifications.notify_dm(
                xp_message.user_id,
                Notification.DM_ON_LOOTBOX_GAIN,
                (
                    f"Congratulations! You have ranked up to **{new_rank}**!\n"
                    "[Log into the website to open your lootbox!](https://genji.pk/lootbox)"
                ),
            )

            await self.bot.notifications.notify_channel_default_to_no_ping(
                self.xp_channel,
                xp_message.user_id,
                Notification.PING_ON_COMMUNITY_RANK_UPDATE,
                f"<:_:976468395505614858> {user.display_name} has ranked up! **{old_rank}** -> **{new_rank}**\n",
            )

        if xp_data.prestige_change:
            for _ in range(15):
                await self.bot.api.grant_active_key_to_user(xp_message.user_id)

            old_rank = " ".join((xp_data.old_main_tier_name, xp_data.old_sub_tier_name))
            new_rank = " ".join((xp_data.new_main_tier_name, xp_data.new_sub_tier_name))

            await self._update_xp_roles_for_user(
                xp_message.user_id, xp_data.old_main_tier_name, xp_data.new_main_tier_name
            )
            await self._update_xp_prestige_roles_for_user(
                xp_message.user_id, xp_data.old_prestige_level, xp_data.new_prestige_level
            )

            await self.bot.notifications.notify_dm(
                xp_message.user_id,
                Notification.DM_ON_LOOTBOX_GAIN,
                (
                    f"Congratulations! You have prestiged up to **{xp_data.new_prestige_level}**!\n"
                    "[Log into the website to open your 15 lootboxes!](https://genji.pk/lootbox)"
                ),
            )

            await self.bot.notifications.notify_channel_default_to_no_ping(
                self.xp_channel,
                xp_message.user_id,
                Notification.PING_ON_COMMUNITY_RANK_UPDATE,
                (
                    f"<:_:976468395505614858><:_:976468395505614858><:_:976468395505614858> "
                    f"{user.display_name} has prestiged! "
                    f"**Prestige {xp_data.old_prestige_level}** -> **Prestige {xp_data.new_prestige_level}**"
                ),
            )

    async def _update_xp_prestige_roles_for_user(
        self, user_id: int, old_prestige_level: int, new_prestige_level: int
    ) -> None:
        """Update a member's prestige role to reflect a prestige level change.

        Args:
            user_id (int): ID of the member to update.
            old_prestige_level (int): Previously held prestige level.
            new_prestige_level (int): Newly achieved prestige level.

        Raises:
            ValueError: If the prestige roles cannot be found.
        """
        old_prestige_role = utils.get(self.guild.roles, name=f"Prestige {old_prestige_level}")
        new_prestige_role = utils.get(self.guild.roles, name=f"Prestige {new_prestige_level}")
        if not (old_prestige_role or new_prestige_role):
            log.debug(
                f"Old prestige level: {old_prestige_level}\n"
                f"New prestige level: {new_prestige_level}\nUser ID: {user_id}"
            )
            raise ValueError("Can't update xp prestige roles for user.")
        assert old_prestige_role and new_prestige_role
        member = self.guild.get_member(user_id)
        if not member:
            return
        roles = set(member.roles)
        roles.discard(old_prestige_role)
        roles.add(new_prestige_role)
        await member.edit(roles=list(roles))

    async def _update_xp_roles_for_user(self, user_id: int, old_tier_name: str, new_tier_name: str) -> None:
        """Update a member's rank role to reflect a tier change.

        Args:
            user_id (int): ID of the member to update.
            old_tier_name (str): Name of the previous main tier role.
            new_tier_name (str): Name of the new main tier role.

        Raises:
            ValueError: If the rank roles cannot be found.
        """
        old_rank = utils.get(self.guild.roles, name=old_tier_name)
        new_rank = utils.get(self.guild.roles, name=new_tier_name)
        if not (old_rank or new_rank):
            log.debug(f"Old tier name: {old_tier_name}\nNew tier name: {new_tier_name}\nUser ID: {user_id}")
            raise ValueError("Can't update xp roles for user.")
        assert old_rank and new_rank
        member = self.guild.get_member(user_id)
        if not member:
            return
        roles = set(member.roles)
        roles.discard(old_rank)
        roles.add(new_rank)
        await member.edit(roles=list(roles))

    async def grant_user_xp_of_type(self, user_id: int, xp_type: XP_TYPES) -> None:
        """Grant XP of a specific type to a user and emit notifications.

        Creates an `XpGrantRequest` from the configured amount for the given type,
        applies it via the API, and triggers the notification flow.

        Args:
            user_id (int): ID of the user receiving XP.
            xp_type (XP_TYPES): Type/category of the XP grant.
        """
        data = XpGrantRequest(XP_AMOUNTS[xp_type], xp_type)
        await self.bot.api.grant_user_xp(user_id, data)

    @register_queue_handler("api.xp.grant")
    async def _process_xp_grant(self, message: AbstractIncomingMessage) -> None:
        """Handle a RabbitMQ message to create a playtest thread.

        Decodes the message and initiates the playtest creation process via
        `add_playtest`.

        Args:
            message (AbstractIncomingMessage): The message containing the playtest creation payload.

        Raises:
            msgspec.ValidationError: If the message body cannot be decoded to the expected format.
        """
        try:
            struct = msgspec.json.decode(message.body, type=XpGrantEvent)
            if message.headers.get("x-pytest-enabled", False):
                log.debug("Pytest message received.")
                return

            log.debug(f"[x] [RabbitMQ] Processing XP message: {struct.user_id}")

            await self._process_xp_notification(struct)
        except Exception as e:
            raise e


class XPCog(commands.GroupCog, group_name="xp"):
    def __init__(self, bot: core.Genji) -> None:
        """Initialize XPCog."""
        self.bot = bot

    @app_commands.command(name="grant")
    async def _command_grant_xp(
        self,
        itx: GenjiItx,
        user: app_commands.Transform[int, transformers.UserTransformer],
        amount: app_commands.Range[int, 1],
    ) -> None:
        """Grant user XP."""
        user_data = await self.bot.api.get_user(user)
        nickname = user_data.coalesced_name if user_data else "Unknown User"
        await itx.response.send_message(f"Granting user {nickname} {amount} XP.", ephemeral=True)
        data = XpGrantRequest(amount, "Other")
        await self.bot.api.grant_user_xp(user, data)


async def setup(bot: core.Genji) -> None:
    """Initialize and attach the XP manager to the bot.

    Args:
        bot (core.Genji): The running bot instance.
    """
    bot.xp = XPService(bot)
    await bot.add_cog(XPCog(bot))
