import asyncio
import contextlib
import logging
import os
from typing import Iterator

import discord
import sentry_sdk

import core

SENTRY_DSN = os.getenv("SENTRY_DSN")
BOT_ENVIRONMENT = os.getenv("BOT_ENVIRONMENT")


class RemoveNoise(logging.Filter):
    def __init__(self) -> None:
        super().__init__(name="discord.state")

    def filter(self, record: logging.LogRecord) -> bool:
        """Remove noisy discord.state logs."""
        return not (record.levelname == "WARNING" and "referencing an unknown" in record.msg)


class RemoveShardCloseNoise(logging.Filter):
    def __init__(self) -> None:
        super().__init__(name="discord.client")

    def filter(self, record: logging.LogRecord) -> bool:
        """Remove noisy discord.client logs."""
        return not (record.exc_info and discord.errors.ConnectionClosed in record.exc_info)


@contextlib.contextmanager
def setup_logging() -> Iterator[None]:
    """Set up logging."""
    log = logging.getLogger()

    try:
        discord.utils.setup_logging()
        logging.getLogger("discord").setLevel(logging.INFO)
        logging.getLogger("discord.http").setLevel(logging.WARNING)
        logging.getLogger("discord.state").addFilter(RemoveNoise())
        logging.getLogger("discord.client").addFilter(RemoveShardCloseNoise())
        log.setLevel(logging.INFO)
        yield None
    finally:
        handlers = log.handlers[:]
        for hdlr in handlers:
            hdlr.close()
            log.removeHandler(hdlr)


async def main() -> None:
    """Start the bot instance."""
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,
        traces_sample_rate=1.0,
        profile_session_sample_rate=1.0,
        environment=BOT_ENVIRONMENT,
    )
    logging.getLogger("discord.gateway").setLevel("WARNING")
    prefix = "?" if BOT_ENVIRONMENT == "production" else "!"
    bot = core.Genji(prefix=prefix)

    async with bot:
        await bot.start(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    with setup_logging():
        asyncio.run(main())
