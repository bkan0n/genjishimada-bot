from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord import Guild, MediaGalleryItem, Member, Role, ui
from discord.ext import commands

from utilities.base import BaseCog

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiCtx, GenjiItx


COMPLETION_SUBMISSIONS_INFO = """
To get promoted in **Genji Parkour**, follow these steps:

1. Complete a Genji Parkour map that is in the current map pool.
2. Use the `/submit-completion` command in <#1072898844339224627>.
    - _Note: Maps that aren't currently accepted won't appear in the map code field._
3. Your submission will go through a verification process.
4. Once verified, you'll receive a notification.

- By using the `time` argument, you can track your personal bests and compare them to others using the `/completions` command.
- Additionally, you must rate the quality of the map. Use the `quality` argument to rate the map on a scale from 1 to 6:
    - 6: Excellent
    - 5: Great
    - 4: Good
    - 3: Average
    - 2: Subpar
    - 1: Poor
"""  # noqa: E501

RANKS_INFO = """
- Ranks do not need to be acquired in order.
- To receive a rank you must complete the required amount of maps for that difficulty/rank.
- See image below for rank thresholds.
"""
RANKS_INFO_IMAGE = "https://bkan0n.com/assets/images/rank_chart_landscape.png"

MEDALS_INFO = """
- To get a +, ++ or +++ rank, you must obtain the same amount of **Bronze**, **Silver**, or **Gold** medals as the rank normally requires (see image below).
- You _must_ post a completion which includes a `time` and a `video` URL showing your run.
- You will get a icon next to your name if you have a plus (+, ++, +++) rank!
- Once verified, you'll automatically receive your medal.
- If medals are added to a map after you have already submitted, you will still get credit.
"""  # noqa: E501
MEDALS_INFO_IMAGE = "https://bkan0n.com/assets/images/rank_chart_landscape.png"


COMPLETION_SUBMISSION_RULES = """
**Completion Requirements/Guidelines:**
- Map code in the screenshot must match the map code in the bot.
- Time must be displayed in either the Top 5 leaderboard, or as the announcement in the middle of the screen. If video submission, it must show both.
- You cannot use edit the map in anyway using Custom Games settings, Workshop Settings, or any other Workshop code. This includes but is not limited to changing tech bans, gravity, etc.
- You are not allowed to use scripts, macros, or anything similar to complete any portion of a map.
- You may not used a banned tech (restricted via map author/listed in @GenjiBot#9209) where the ban is non-functional due to Workshop bugs.

*Records Only:*
- Time must be fully visible from 0.00 to the finish. Do not fade in or out while the timer is running.
- Video proof is **required** for *World Records* and *Medals*.
- Cuts in the video are **not** allowed (between 0.00 and finish).
- Game sound is **not** required.
- Editing before and after is allowed but it ***cannot*** interfere with timer or any ability to *validate* the submission.

**Senseis reserve the right to deny any submission for any reason, regardless if it is listed here or not.**
"""  # noqa: E501


MAP_SUBMISSIONS_INFO = """
The process is simple. Start by typing the following command in any channel that you can type in:
`/submit-map`
Three required arguments will be necessary enter the command with an additional five optional arguments. Discord will highlight the inputs in red if it's invalid or missing.

__REQUIRED ARGUMENTS:__
- `code`: Overwatch Workshop code
- `map_name`: Overwatch Map
- `checkpoint_count`: Number of checkpoints your map has
- `category`: Map type/category.

__OPTIONAL ARGUMENTS:__
- `description`: Extra information or details you want to add to the map
- `guide_url`: a valid URL to a guide for the map
- `gold`: Time to beat for a *Gold* medal
- `silver`: Time to beat for a *Silver* medal
- `bronze`: Time to beat for a *Bronze* medal
- `custom_title`: A custom title for your map.
- `custom_banner` A custom banner instead of the default map banner.

Once you enter the command, dropdown boxes will appear.
You must select a map type and a difficulty. If there are mechanics or restrictions, you can select multiple of those.

When you finish selecting those options, you can continue with the *green* button. Or you can cancel the process with the red button.
A final overview will appear where you can double check the data you have entered. If it is all correct, then press the *green* button. If not, click the red button to cancel the process.
Once submitted, the map must go through a playtesting phase.
"""  # noqa: E501

MAP_SUBMISSIONS_INFO_IMAGE = "https://bkan0n.com/assets/images/map_submission_1.png"

MAP_PLAYTESTING_INFO = """
:bangbang: You _must_ have submitted a completion for the map to vote :bangbang:

- Each difficulty requires a specific amount of *votes* **and** *completion submissions*.
- Creators cannot vote for their map as their map submission contains their best estimate of difficulty.
- Playtesters will give the creator tips on how to make the map better, or what specifically needs to change, if there are any glaring issues, etc.
"""  # noqa: E501
MAP_PLAYTESTING_INFO_IMAGE = "https://bkan0n.com/assets/images/map_submit_flow.png"

DIFF_TECH_CHART_IMAGE = "https://bkan0n.com/assets/images/diff_techs.png"


class InformationButton(ui.Button):
    def __init__(
        self,
        *,
        label: str,
        response_view: ui.LayoutView,
        emoji: discord.Emoji | discord.PartialEmoji | str | None = None,
        row: int = 0,
    ) -> None:
        """Initialize an information button.

        Creates a grey-styled button with a label, optional emoji, and row
        placement. The button's `custom_id` is derived from the label by
        stripping special characters. When pressed, it will display the
        provided response view ephemerally.

        Args:
            label: Button label text.
            response_view: The view to send back when the button is pressed.
            emoji: Optional emoji to display on the button.
            row: Row index to place the button in. Defaults to 0.
        """
        super().__init__(
            style=discord.ButtonStyle.grey,
            label=label,
            custom_id=re.sub(r"[\s:()?!&\']", "", label.lower()),
            emoji=emoji,
            row=row,
        )
        self.response_view = response_view

    async def callback(self, itx: GenjiItx) -> None:
        """Handle button press to show the response view.

        Sends the associated `response_view` ephemerally to the user.

        Args:
            itx: The interaction context for this button press.
        """
        await itx.response.send_message(view=self.response_view, ephemeral=True)


class GenericInformationView(ui.LayoutView):
    def __init__(self, *, title: str, content: str | None = None, image_url: str | None = None) -> None:
        """Initialize a generic information view.

        Creates a view with a short timeout that can display a title, optional
        content text, and an optional image. Automatically builds its layout
        by calling `rebuild_components`.

        Args:
            title: The title text to display.
            content: Optional content/body text.
            image_url: Optional image URL to display in a media gallery.
        """
        super().__init__(timeout=10)
        self.title = title
        self.content = content
        self.image_url = image_url
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the UI components of the information view.

        Clears all items and repopulates them with a container consisting of:
        a text display showing the title and content, a separator, and an
        optional media gallery if `image_url` is set.
        """
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay(f"# {self.title}\n{self.content if self.content else ''}"),
            ui.Separator(),
            *(ui.MediaGallery(MediaGalleryItem(self.image_url)),) if self.image_url else (),
        )
        self.add_item(container)


class CompletionInformationView(ui.LayoutView):
    def __init__(self) -> None:
        """Initialize the MapInformationView."""
        super().__init__(timeout=None)
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the necessary components."""
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay("# Completions Information\nClick the buttons below to learn more!"),
            ui.Separator(),
            ui.ActionRow(
                InformationButton(
                    label="How to submit?",
                    response_view=GenericInformationView(title="How to submit?", content=COMPLETION_SUBMISSIONS_INFO),
                ),
                InformationButton(
                    label="Submission Rules",
                    response_view=GenericInformationView(title="Submission Rules", content=COMPLETION_SUBMISSION_RULES),
                ),
                InformationButton(
                    label="Rank Info & Thresholds",
                    response_view=GenericInformationView(
                        title="Rank Info & Thresholds", content=RANKS_INFO, image_url=RANKS_INFO_IMAGE
                    ),
                ),
                InformationButton(
                    label="Medals Info & Thresholds",
                    response_view=GenericInformationView(
                        title="Medals Info & Thresholds", content=MEDALS_INFO, image_url=MEDALS_INFO_IMAGE
                    ),
                ),
            ),
        )
        self.add_item(container)


class MapInformationView(ui.LayoutView):
    def __init__(self) -> None:
        """Initialize the MapInformationView."""
        super().__init__(timeout=None)
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the necessary components."""
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay("# Map Submission / Playtest Information\nClick the buttons below to learn more!"),
            ui.Separator(),
            ui.ActionRow(
                InformationButton(
                    label="How to submit?",
                    response_view=GenericInformationView(
                        title="How to submit?", content=MAP_SUBMISSIONS_INFO, image_url=MAP_SUBMISSIONS_INFO_IMAGE
                    ),
                ),
                InformationButton(
                    label="Playtesting Info",
                    response_view=GenericInformationView(
                        title="Playtesting Info", content=MAP_PLAYTESTING_INFO, image_url=MAP_PLAYTESTING_INFO_IMAGE
                    ),
                ),
                InformationButton(
                    label="Difficulty & Techs Info",
                    response_view=GenericInformationView(
                        title="Difficulty / Tech Chart", image_url=DIFF_TECH_CHART_IMAGE
                    ),
                ),
            ),
        )
        self.add_item(container)


class ServerRoleToggleButton(ui.Button):
    role: Role
    guild: Guild

    def __init__(self, *, bot: Genji, label: str, role_id: int, emoji: str | None = None) -> None:
        self.bot = bot
        self.role_id = role_id
        super().__init__(
            label=label,
            style=discord.ButtonStyle.gray,
            custom_id=label.lower().replace(" ", "_") + "_server_role_toggle",
            emoji=emoji,
        )

    def _set_guild_and_role(self) -> None:
        if not self.guild or not self.role:
            _guild = self.bot.get_guild(self.bot.config.guild)
            assert _guild
            self.guild = _guild
            _role = _guild.get_role(self.role_id)
            assert _role
            self.role = _role

    async def add_remove_roles(self, member: Member) -> bool:
        """Add or remove roles (toggle-like behavior)."""
        if self.role in member.roles:
            await member.remove_roles(self.role)
            return False
        else:
            await member.add_roles(self.role)
            return True

    async def callback(self, itx: GenjiItx) -> None:
        """Add role upon button click."""
        await itx.response.defer(ephemeral=True, thinking=True)
        self._set_guild_and_role()
        assert isinstance(itx.user, Member)
        res = await self.add_remove_roles(itx.user)
        await itx.edit_original_response(content=f"{self.role.name} {'added' if res else 'removed'}")


class ServerRoleSelectView(ui.LayoutView):
    def __init__(self, bot: Genji) -> None:
        """Initialize the MapInformationView."""
        self.bot = bot
        super().__init__(timeout=None)
        self.rebuild_components()

    def rebuild_components(self) -> None:
        """Rebuild the necessary components."""
        self.clear_items()
        container = ui.Container(
            ui.TextDisplay("# Role Customization\n-# You can also adjust these roles here <id:customize>"),
            ui.Separator(),
            ui.TextDisplay("### Announcement Pings"),
            ui.ActionRow(
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="General Announcements",
                    role_id=self.bot.config.roles.mentionable.general_announcements,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Framework Patch Notes",
                    role_id=self.bot.config.roles.mentionable.framework_patch_notes,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Website/Bot Patch Notes",
                    role_id=self.bot.config.roles.mentionable.website_patch_notes,
                ),
            ),
            ui.Separator(),
            ui.TextDisplay("### Regions"),
            ui.ActionRow(
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="North America",
                    role_id=self.bot.config.roles.location.north_america,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Europe",
                    role_id=self.bot.config.roles.location.europe,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Asia",
                    role_id=self.bot.config.roles.location.asia,
                ),
            ),
            ui.ActionRow(
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Oceana",
                    role_id=self.bot.config.roles.location.oceana,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="South America",
                    role_id=self.bot.config.roles.location.south_america,
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Africa",
                    role_id=self.bot.config.roles.location.africa,
                ),
            ),
            ui.Separator(),
            ui.TextDisplay("### Platform"),
            ui.ActionRow(
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="Console",
                    role_id=self.bot.config.roles.platform.console,
                    emoji="🎮",
                ),
                ServerRoleToggleButton(
                    bot=self.bot,
                    label="PC",
                    role_id=self.bot.config.roles.platform.pc,
                    emoji="⌨️",
                ),
            ),
        )
        self.add_item(container)


class InformationPagesCog(BaseCog):
    @commands.command()
    @commands.is_owner()
    async def completioninfo(self, ctx: GenjiCtx) -> None:
        """Add the completion info view to a message."""
        await ctx.message.delete(delay=1)
        await ctx.send(view=CompletionInformationView())

    @commands.command()
    @commands.is_owner()
    async def mapsubmissioninfo(self, ctx: GenjiCtx) -> None:
        """Add the map submission info view to a message."""
        await ctx.message.delete(delay=1)
        await ctx.send(view=MapInformationView())

    @commands.command()
    @commands.is_owner()
    async def roleselect(self, ctx: GenjiCtx) -> None:
        """Add the map submission info view to a message."""
        await ctx.message.delete(delay=1)
        await ctx.send(view=ServerRoleSelectView(ctx.bot))


async def setup(bot: Genji) -> None:
    """Load the InformationPagesCog cog."""
    await bot.add_cog(InformationPagesCog(bot))
    bot.add_view(CompletionInformationView())
    bot.add_view(MapInformationView())
    bot.add_view(ServerRoleSelectView(bot))


async def teardown(bot: Genji) -> None:
    """Unload the InformationPagesCog cog."""
    await bot.remove_cog("InformationPagesCog")
