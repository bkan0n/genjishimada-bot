from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from genjipk_sdk.models import Notification

if TYPE_CHECKING:
    import discord

    import core

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: core.Genji) -> None:
        """Initialize NotificationService."""
        self._bot = bot

    async def _get_notification_flags(self, user_id: int) -> Notification:
        resp = await self._bot.session.get(
            f"http://localhost:8000/api/v3/users/{user_id}/notifications?to_bitmask=true"
        )
        resp.raise_for_status()
        data = await resp.json()
        bitmask = data["notifications"]
        if bitmask is None:
            return Notification.NONE
        return Notification(bitmask)

    async def should_notify(self, user_id: int, notification: Notification) -> bool:
        """Check if a user has allowed notifications for this particular process."""
        flags = await self._bot.api.get_notification_flags(user_id)
        # Bitwise AND: returns non-zero if the notification flag is enabled.
        result = bool(flags & notification)
        logger.debug("User %s: Checking %s: %s", user_id, notification.name, result)
        return result

    async def notify_dm(self, user_id: int, notification: Notification, message: str) -> bool:
        """Send a DM to the user if the given notification type is enabled."""
        if await self.should_notify(user_id, notification):
            try:
                user = self._bot.get_user(user_id)
                assert user
                await user.send(message)
                logger.debug("Sent DM to user %s for %s", user_id, notification.name)
                return True
            except Exception as e:
                logger.error("Failed to send DM to user %s: %s", user_id, e)
        else:
            logger.debug("User %s does not have %s enabled; DM not sent.", user_id, notification.name)
        return False

    async def notify_channel(
        self,
        channel: discord.TextChannel | discord.Thread,
        user_id: int,
        notification: Notification,
        message: str,
        **kwargs,
    ) -> bool:
        """Send a message in the channel that pings the user if the notification is enabled."""
        if await self.should_notify(user_id, notification):
            try:
                await channel.send(f"<@{user_id}> {message}", **kwargs)
                logger.debug("Sent channel notification to user %s for %s", user_id, notification.name)
                return True
            except Exception as e:
                logger.error("Failed to send channel notification for user %s: %s", user_id, e)
        else:
            logger.debug("User %s does not have %s enabled; channel notification not sent.", user_id, notification.name)
        return False

    async def notify_channel_default_to_no_ping(
        self,
        channel: discord.TextChannel | discord.Thread,
        user_id: int,
        notification: Notification,
        message: str,
        **kwargs,
    ) -> None:
        """Send a message in the channel that pings the user, or sends message without a ping."""
        success = await self.notify_channel(channel, user_id, notification, message)
        if not success:
            if not channel:
                return
            await channel.send(message, **kwargs)


async def setup(bot: core.Genji) -> None:
    """Setup Notification extension."""
    bot.notifications = NotificationService(bot)
