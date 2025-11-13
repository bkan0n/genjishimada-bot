from __future__ import annotations

import typing

import discord
from discord.ext import commands

from utilities.base import BaseCog

if typing.TYPE_CHECKING:
    import core
    from utilities._types import GenjiCtx, GenjiItx


async def setup(bot: core.Genji) -> None:
    """Add the ticketing Cog to the Discord bot.

    Registers the ModmailCog cog so ticket creation and moderation utilities
    are available.

    Args:
        bot: The Genji Discord bot instance.
    """
    await bot.add_cog(ModmailCog(bot))


class TicketStart(discord.ui.View):
    def __init__(self) -> None:
        """Initialize the ticket launcher view.

        Creates a persistent view with no timeout so the “Create Ticket” button
        remains available across restarts.
        """
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Click me to get help from a Sensei!",
        custom_id="create_ticket",
        style=discord.ButtonStyle.grey,
        emoji="🆘",
    )
    async def create_ticket(self, itx: GenjiItx, button: discord.ui.Button) -> None:
        """Open a modal to start the ticket creation flow.

        Presents a modal to the user for entering the ticket subject, then waits
        for the modal submission before continuing.

        Args:
            itx: The component interaction context.
            button: The pressed button component.
        """
        modal = TicketStartModal()
        await itx.response.send_modal(modal)
        await modal.wait()


class TicketStartModal(discord.ui.Modal):
    def __init__(self) -> None:
        """Initialize the ticket creation modal.

        Sets the modal title and a long timeout to allow users ample time to enter
        a subject before submission.
        """
        super().__init__(
            title="Create Ticket",
            timeout=3600,
        )

    subject = discord.ui.TextInput(
        label="Subject",
        style=discord.TextStyle.short,
        placeholder="You will be able to share more info later.",
        required=True,
        max_length=100,
    )

    async def on_submit(self, itx: GenjiItx) -> None:
        """Create a ticket thread after modal submission.

        Validates the subject, acknowledges the user ephemerally, creates a private
        modmail thread in the configured channel, adds the requester, pings the
        modmail role, and posts initial instructions with a close button.

        Args:
            itx: The interaction context associated with the modal submission.
        """
        if self.subject.value is None:
            return

        await itx.response.send_message(content="Creating ticket...", ephemeral=True)
        channel = itx.client.get_channel(itx.client.config.channels.help.modmail)
        assert isinstance(channel, discord.TextChannel)
        thread = await channel.create_thread(
            name=f"{itx.user.display_name[:10]} | {self.subject.value[:80]}",
            message=None,
            type=None,
            invitable=False,
        )
        await thread.add_user(itx.user)
        assert itx.guild
        modmail_role = itx.guild.get_role(itx.client.config.roles.mentionable.modmail)
        assert modmail_role

        await thread.send(
            content=(
                f"{itx.user.mention}\n"
                f"# {self.subject.value}\n"
                "Please describe your issue here.\n"
                "Be sure to include any images or other details.\n\n"
                "### If the issue is resolved, please use `?solved` to close the ticket\n\n"
                "`------------------`\n"
                f"{modmail_role.mention}"
            ),
            view=CloseTicketView(),
        )

    async def on_error(self, itx: GenjiItx, error: Exception) -> None:
        """Handle errors raised during modal submission.

        If the initial response has not been sent, replies ephemerally with a
        generic error message to prevent the interaction from timing out.

        Args:
            itx: The interaction context associated with the modal submission.
            error: The exception raised during handling.
        """
        if itx.response.is_done():
            ...
        else:
            await itx.response.send_message("Oops! Something went wrong.", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self) -> None:
        """Initialize the close-ticket view.

        Creates a persistent view with no timeout so the “Close Ticket” button
        continues to function after process restarts.
        """
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, itx: GenjiItx, button: discord.ui.Button) -> None:
        """Archive and lock the current ticket thread.

        Marks the thread as resolved by locking and archiving it.

        Args:
            itx: The component interaction context for the close action.
            button: The pressed button component.
        """
        assert isinstance(itx.channel, discord.Thread)
        await itx.channel.edit(locked=True, archived=True)


def ticket_thread_check():  # noqa: ANN201
    """Restrict a command to ticket threads in the configured modmail channel.

    Builds a `commands.check` predicate ensuring the command is invoked
    inside a thread whose parent matches the configured modmail channel ID.

    Returns:
        A `commands.check` predicate usable as a decorator.
    """

    def predicate(ctx: GenjiCtx) -> bool:
        return isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == ctx.bot.config.channels.help.modmail

    return commands.check(predicate)


class ModmailCog(BaseCog):
    async def cog_load(self) -> None:
        """Register persistent views when the cog is loaded.

        Adds the `CloseTicketView` so Discord can route button interactions
        after process restarts.
        """
        self.bot.add_view(CloseTicketView())
        self.bot.add_view(TicketStart())

    @commands.command()
    @commands.is_owner()
    async def setup_tickets(
        self,
        ctx: commands.Context[core.Genji],
    ) -> None:
        """Post the ticket creation panel with a button.

        Owner-only command that sends an instructional message and attaches the
        `TicketStart` view to let users open a ticket.

        Args:
            ctx: The command invocation context.
        """
        await ctx.channel.send(
            content=(
                "# Do you require private assistance from a Sensei?\n"
                "### Press the button below for any of the following: \n"
                "- **High priority** bugs found regarding:\n"
                "  - GenjiBot\n"
                "  - Official Genji Parkour Framework\n"
                "- Other users\n"
                "- Sensitive information\n\n"
                "## **Using this system will create a private thread only Senseis can see.**"
            ),
            view=TicketStart(),
        )

    @commands.command()
    @ticket_thread_check()
    async def solved(
        self,
        ctx: commands.Context[core.Genji],
    ) -> None:
        """Mark the current ticket as solved and close the thread.

        Adds a confirmation reaction to the invoking message, then archives and
        locks the thread to prevent further replies.

        Args:
            ctx: The command invocation context.
        """
        assert isinstance(ctx.channel, discord.Thread)
        await ctx.message.add_reaction("<:_:895727516017393665>")
        await ctx.channel.edit(archived=True, locked=True)
