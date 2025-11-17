# Adapted from RoboDanny (Rapptz)

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, Annotated, Any, Iterable, Literal, Optional, Sequence, TypedDict

import discord
from discord import Message, app_commands
from discord.ext import commands
from genjipk_sdk.tags import (
    OpAlias,
    OpClaim,
    OpCreate,
    OpEdit,
    OpIncrementUsage,
    OpPurge,
    OpRemove,
    OpRemoveById,
    OpTransfer,
    TagRowDTO,
    TagsAutocompleteRequest,
    TagsAutocompleteResponse,
    TagsMutateRequest,
    TagsMutateResponse,
    TagsSearchFilters,
    TagsSearchResponse,
)

from utilities.base import ConfirmationView

from .tags_paginator import SimplePages

if TYPE_CHECKING:
    from core import Genji
    from utilities._types import GenjiCtx, GenjiItx


async def check_guild_permissions(ctx: GenjiCtx, perms: dict[str, bool], *, check=all) -> bool:  # noqa: ANN001
    """Check whether the invoking user has the specified guild permissions.

    This helper respects the bot owner's override, denies in DMs, and compares
    the member's resolved guild permissions against the provided mapping using
    the given aggregator.

    Args:
        ctx: Invocation context.
        perms: Mapping of permission name to required boolean value.
        check: Aggregation function applied to the per-permission comparisons,
            for example all (default) or any.

    Returns:
        True if the user satisfies the permission check, otherwise False.
    """
    is_owner = await ctx.bot.is_owner(ctx.author)
    if is_owner:
        return True
    if ctx.guild is None:
        return False
    resolved = ctx.author.guild_permissions  # pyright: ignore[reportAttributeAccessIssue]
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_guild_permissions(*, check=all, **perms: bool) -> Any:  # noqa: ANN001, ANN401
    """Create a commands.check that ensures the invoker has given guild permissions.

    Wraps check_guild_permissions with a provided aggregator and a set of
    required permission flags.

    Keyword Args:
        check: Aggregation function over permission comparisons, e.g. all or any.
        **perms: Permission names mapped to required boolean values.

    Returns:
        A discord.py command check predicate.
    """

    async def pred(ctx: GenjiCtx) -> bool:
        return await check_guild_permissions(ctx, perms, check=check)

    return commands.check(pred)


class TagEntry(TypedDict):
    id: int
    name: str
    content: str


class TagAllFlags(commands.FlagConverter):
    text: bool = commands.flag(default=False, description="Whether to dump the tags as a text file.")


class TagPageEntry:
    __slots__ = ("id", "name")

    def __init__(self, entry: TagEntry) -> None:
        """Initialize a display-only page entry for a tag.

        Args:
            entry: A raw tag entry containing id and name.
        """
        self.id: int = entry["id"]
        self.name: str = entry["name"]

    def __str__(self) -> str:
        """Return a human-readable label for the page entry.

        Returns:
            A string of the form "name (ID: <id>)".
        """
        return f"{self.name} (ID: {self.id})"


class TagPages(SimplePages):
    def __init__(self, entries: list[TagEntry], *, ctx: GenjiCtx, per_page: int = 12) -> None:
        """Initialize a paginator for tag entries.

        Converts raw tag rows into TagPageEntry objects and forwards them to the
        base paginator.

        Args:
            entries: Raw tag rows to paginate.
            ctx: Invocation context used by the paginator for sending pages.
            per_page: Number of entries per page.
        """
        converted = [TagPageEntry(entry) for entry in entries]
        super().__init__(converted, per_page=per_page, ctx=ctx)


class TagName(commands.clean_content):
    def __init__(self, *, lower: bool = False) -> None:
        """Initialize the TagName converter.

        Args:
            lower: Whether to normalize and return the value in lowercase.
        """
        self.lower: bool = lower
        super().__init__()

    async def convert(self, ctx: GenjiCtx, argument: str) -> str:
        """Convert and validate a tag name from user input.

        Performs cleaning, length checks, and prevents names that clash with
        reserved subcommands.

        Args:
            ctx: Invocation context.
            argument: The raw user-provided tag name.

        Raises:
            commands.BadArgument: If the name is empty, too long, or starts with a
                reserved tag group subcommand.

        Returns:
            The cleaned tag name, optionally lowercased based on configuration.
        """
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()
        if not lower:
            raise commands.BadArgument("Missing tag name.")
        if len(lower) > 100:  # noqa: PLR2004
            raise commands.BadArgument("Tag name is a maximum of 100 characters.")
        first_word, _, _ = lower.partition(" ")
        root: commands.GroupMixin = ctx.bot.get_command("tag")  # type: ignore
        if first_word in root.all_commands:
            raise commands.BadArgument("This tag name starts with a reserved word.")
        return converted.strip() if not self.lower else lower


class TagEditModal(discord.ui.Modal, title="Edit Tag"):
    interaction: GenjiItx
    text: str
    content = discord.ui.TextInput(
        label="Tag Content",
        required=True,
        style=discord.TextStyle.long,
        min_length=1,
        max_length=2000,
    )

    def __init__(self, text: str) -> None:
        """Construct the tag edit modal with prefilled content.

        Args:
            text: Existing tag content to prefill in the modal.
        """
        super().__init__()
        self.content.default = text

    async def on_submit(self, interaction: GenjiItx) -> None:
        """Handle submission of the edit modal.

        Captures the interaction and submitted text, then stops the modal wait.

        Args:
            interaction: The interaction that submitted the modal.
        """
        self.interaction = interaction
        self.text = str(self.content)
        self.stop()


class TagMakeModal(discord.ui.Modal, title="Create New Tag"):
    name = discord.ui.TextInput(label="Name", required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label="Content",
        required=True,
        style=discord.TextStyle.long,
        min_length=1,
        max_length=2000,
    )

    def __init__(self, cog: Tags, ctx: GenjiCtx) -> None:
        """Construct the tag creation modal.

        Args:
            cog: The Tags cog instance handling creation.
            ctx: The original command context initiating the modal.
        """
        super().__init__()
        self.cog: Tags = cog
        self.ctx: GenjiCtx = ctx

    async def on_submit(self, interaction: GenjiItx) -> None:
        """Validate and forward modal submission to tag creation.

        Validates the provided name and content. Sends user-facing errors
        ephemerally on validation failures. On success, dispatches to the cog's
        create_tag handler.

        Args:
            interaction: The interaction that submitted the modal.

        Raises:
            commands.BadArgument: If the provided name fails validation.
        """
        assert interaction.guild_id is not None
        name = str(self.name)
        try:
            name = await TagName().convert(self.ctx, name)
        except commands.BadArgument as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            raise e
            return
        except Exception as e:
            raise e
        if self.cog.is_tag_being_made(interaction.guild_id, name):
            await interaction.response.send_message("This tag is already being made by someone else.", ephemeral=True)
            return
        self.ctx.interaction = interaction
        content = str(self.content)
        if len(content) > 2000:  # noqa: PLR2004
            await interaction.response.send_message("Tag content is a maximum of 2000 characters.", ephemeral=True)
        else:
            await self.cog.create_tag(self.ctx, name, content)


class Tags(commands.Cog):
    def __init__(self, bot: Genji) -> None:
        """Initialize the Tags cog.

        Args:
            bot: The bot instance.
        """
        self.bot: Genji = bot
        self._reserved_tags_being_made: dict[int, set[str]] = {}

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        """A small label emoji used to represent this cog.

        Returns:
            A PartialEmoji used in UI elements.
        """
        return discord.PartialEmoji(name="\N{LABEL}\ufe0f")

    async def cog_command_error(self, ctx: GenjiCtx, error: commands.CommandError) -> None:
        """Common command error handler for this cog.

        Shows command help for tag group misuse, forwards validation errors to
        the channel, and formats flag parsing errors.

        Args:
            ctx: Invocation context where the error occurred.
            error: The raised command error.
        """
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            if ctx.command.qualified_name == "tag":  # pyright: ignore[reportOptionalMemberAccess]
                await ctx.send_help(ctx.command)
            else:
                await ctx.send(str(error))
        elif isinstance(error, commands.FlagError):
            await ctx.send(str(error))

    async def _api_search(  # noqa: PLR0913
        self,
        guild_id: int,
        *,
        name: str | None = None,
        fuzzy: bool = False,
        owner_id: int | None = None,
        include_content: bool = False,
        include_rank: bool = False,
        include_aliases: bool = True,
        only_aliases: bool = False,
        random: bool = False,
        by_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: Literal["name", "uses", "created_at"] = "name",
        sort_dir: Literal["asc", "desc"] = "asc",
    ) -> TagsSearchResponse:
        """Search for tags via the API with flexible filters.

        This is a thin wrapper around the API's tag search endpoint. It supports
        filtering by name or owner, fuzzy search, alias inclusion, sorting, and
        pagination.

        Args:
            guild_id: The guild whose tags to search.
            name: Exact or fuzzy name to search for.
            fuzzy: Whether to enable fuzzy matching on the name.
            owner_id: Filter results to a specific owner.
            include_content: Whether to include tag content in results.
            include_rank: Whether to include rank metrics.
            include_aliases: Whether to include aliases in the result set.
            only_aliases: If True, return only aliases.
            random: If True, return random results.
            by_id: Look up a specific tag by internal id.
            limit: Maximum number of items to return.
            offset: Offset into the result set for pagination.
            sort_by: Field to sort by ("name", "uses", or "created_at").
            sort_dir: Sort direction ("asc" or "desc").

        Returns:
            A TagsSearchResponse containing matched items and metadata.
        """
        payload = TagsSearchFilters(
            guild_id=guild_id,
            name=name,
            fuzzy=fuzzy,
            include_aliases=include_aliases,
            only_aliases=only_aliases,
            owner_id=owner_id,
            random=random,
            by_id=by_id,
            include_content=include_content,
            include_rank=include_rank,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
        return await self.bot.api.search_tags(payload)

    async def _api_mutate(self, *ops) -> TagsMutateResponse:
        """Perform a tag mutation operation through the API.

        Wraps one or more tag operation objects (OpCreate, OpEdit, OpRemove, etc.)
        into a single mutation request and sends it to the API.

        Args:
            *ops: One or more tag operation instances to include in the request.

        Returns:
            A TagsMutateResponse containing the results of each operation.
        """
        req = TagsMutateRequest(list(ops))
        return await self.bot.api.mutate_tags(req)

    async def _api_autocomplete(
        self,
        guild_id: int,
        mode: Literal["aliased", "non_aliased", "owned_aliased", "owned_non_aliased"],
        q: str,
        *,
        owner_id: int | None = None,
        limit: int = 12,
    ) -> TagsAutocompleteResponse:
        """Perform a tag autocomplete lookup via the API.

        Queries tag names for use in Discord autocomplete menus, supporting
        different modes for alias ownership and visibility.

        Args:
            guild_id: The guild whose tags to search.
            mode: Autocomplete mode ("aliased", "non_aliased",
                "owned_aliased", "owned_non_aliased").
            q: Partial query string for matching tag names.
            owner_id: Filter results by owner ID.
            limit: Maximum number of suggestions to return.

        Returns:
            A TagsAutocompleteResponse from the API.
        """
        req = TagsAutocompleteRequest(guild_id=guild_id, q=q, mode=mode, owner_id=owner_id, limit=limit)
        return await self.bot.api.autocomplete_tags(req)

    async def get_possible_tags(self, guild: discord.abc.Snowflake) -> list[TagEntry]:
        """Fetch all tags for a guild, including content.

        Args:
            guild: Guild-like object providing an id.

        Returns:
            A list of tag entries, each with id, name, and content.
        """
        res = await self._api_search(guild.id, include_content=True, limit=1000)
        return [{"id": r.id, "name": r.name, "content": r.content or ""} for r in res.items]

    async def get_random_tag(self, guild: discord.abc.Snowflake) -> Optional[TagEntry]:
        """Retrieve a single random tag from a guild.

        Args:
            guild: Guild-like object providing an id.

        Returns:
            A tag entry dict containing id, name, and content,
            or None if the guild has no tags.
        """
        res = await self._api_search(guild.id, random=True, include_content=True, limit=1)
        if not res.items:
            return None
        r = res.items[0]
        return {"id": r.id, "name": r.name, "content": r.content or ""}

    async def get_tag(self, guild_id: Optional[int], name: str) -> TagEntry:
        """Retrieve a tag by name for a given guild.

        Performs an exact match search; if no result is found,
        suggested similar names are provided in the error message.

        Args:
            guild_id: The guild to search within.
            name: Tag name to look up.

        Raises:
            RuntimeError: If the tag is not found.

        Returns:
            A tag entry dict containing id, name, and content.
        """
        res = await self._api_search(guild_id or 0, name=name, include_content=True, fuzzy=False, limit=1)
        if not res.items:
            if res.suggestions:
                names = "\n".join(res.suggestions)
                raise RuntimeError(f"Tag not found. Did you mean...\n{names}")
            raise RuntimeError("Tag not found.")
        item = res.items[0]
        return {"id": item.id, "name": item.name, "content": item.content or ""}

    async def create_tag(self, ctx: GenjiCtx, name: str, content: str) -> None:
        """Create a new tag owned by the invoking user.

        Sends a creation operation to the API and handles success or failure
        messages in the invoking context.

        Args:
            ctx: Invocation context containing guild and author information.
            name: The name of the tag.
            content: The tag's content text.
        """
        op = OpCreate(guild_id=ctx.guild.id, name=name, content=content, owner_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        out = await self._api_mutate(op)
        result = out.results[0] if out.results else None
        if not result or not result.ok:
            await ctx.send(result.message if result else "Could not create tag.")
            return
        await ctx.send(f"Tag {name} successfully created.")

    def is_tag_being_made(self, guild_id: int, name: str) -> bool:
        """Check if a tag is currently being made by someone else.

        Args:
            guild_id: ID of the guild where the tag is being created.
            name: Tag name to check.

        Returns:
            True if another user is already in the process of creating the tag.
        """
        return name.lower() in self._reserved_tags_being_made.get(guild_id, set())

    def add_in_progress_tag(self, guild_id: int, name: str) -> None:
        """Mark a tag as being in progress for creation.

        Adds the tag name to the set of reserved tags for the guild.

        Args:
            guild_id: ID of the guild.
            name: Tag name being created.
        """
        self._reserved_tags_being_made.setdefault(guild_id, set()).add(name.lower())

    def remove_in_progress_tag(self, guild_id: int, name: str) -> None:
        """Unmark a tag as in progress for a guild.

        Removes the tag name from the reserved list. Deletes the guild key
        if no tags remain in progress.

        Args:
            guild_id: ID of the guild.
            name: Tag name to clear.
        """
        g = self._reserved_tags_being_made.get(guild_id)
        if not g:
            return
        g.discard(name.lower())
        if not g:
            del self._reserved_tags_being_made[guild_id]

    async def non_aliased_tag_autocomplete(self, interaction: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for non-aliased tags.

        Used in command autocomplete for selecting tags that are not aliases.

        Args:
            interaction: The interaction object invoking the autocomplete.
            current: Partial query string from the user.

        Returns:
            A list of Choice objects representing matching tag names.
        """
        res = await interaction.client.api.autocomplete_tags(
            TagsAutocompleteRequest(guild_id=interaction.guild_id, q=current, mode="non_aliased", limit=12)  # pyright: ignore[reportArgumentType]
        )
        return [app_commands.Choice(name=a, value=a) for a in res.items]

    async def aliased_tag_autocomplete(self, interaction: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for aliased tags.

        Used in command autocomplete for selecting tags that are aliases.

        Args:
            interaction: The interaction object invoking the autocomplete.
            current: Partial query string from the user.

        Returns:
            A list of Choice objects representing matching tag names.
        """
        res = await interaction.client.api.autocomplete_tags(
            TagsAutocompleteRequest(guild_id=interaction.guild_id, q=current, mode="aliased", limit=12)  # pyright: ignore[reportArgumentType]
        )
        return [app_commands.Choice(name=a, value=a) for a in res.items]

    async def owned_non_aliased_tag_autocomplete(
        self, interaction: GenjiItx, current: str
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for tags owned by the user that are not aliases.

        Args:
            interaction: The interaction object invoking the autocomplete.
            current: Partial query string from the user.

        Returns:
            A list of Choice objects representing matching tag names.
        """
        res = await interaction.client.api.autocomplete_tags(
            TagsAutocompleteRequest(
                guild_id=interaction.guild_id,  # pyright: ignore[reportArgumentType]
                q=current,
                mode="owned_non_aliased",
                owner_id=interaction.user.id,
                limit=12,
            )
        )
        return [app_commands.Choice(name=a, value=a) for a in res.items]

    async def owned_aliased_tag_autocomplete(
        self, interaction: GenjiItx, current: str
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for tags owned by the user that are aliases.

        Args:
            interaction: The interaction object invoking the autocomplete.
            current: Partial query string from the user.

        Returns:
            A list of Choice objects representing matching tag names.
        """
        res = await interaction.client.api.autocomplete_tags(
            TagsAutocompleteRequest(
                guild_id=interaction.guild_id,  # pyright: ignore[reportArgumentType]
                q=current,
                mode="owned_aliased",
                owner_id=interaction.user.id,
                limit=12,
            )
        )
        return [app_commands.Choice(name=a, value=a) for a in res.items]

    @commands.hybrid_group(fallback="get")
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.describe(name="The tag to retrieve")
    @app_commands.autocomplete(name=aliased_tag_autocomplete)
    async def tag(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> None:
        """Fetch a tag by name via the `tag` command or its fallback.

        This hybrid command supports both text and slash invocation.

        Args:
            ctx: Invocation context.
            name: Tag name to fetch.
        """
        await self._tag_get(ctx, name=name)

    async def _tag_get(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> Message | None:
        """Internal helper to fetch and display a tag by name.

        Sends the tag's content if found and increments its usage count.

        Args:
            ctx: Invocation context.
            name: Tag name to fetch.
        """
        try:
            tag = await self.get_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]
        except RuntimeError as e:
            return await ctx.send(str(e))
        await ctx.send(tag["content"])

        op = OpIncrementUsage(guild_id=ctx.guild.id, name=tag["name"])  # pyright: ignore[reportOptionalMemberAccess]
        await self._api_mutate(op)

    @tag.command()
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.describe(name="The tag to retrieve")
    @app_commands.autocomplete(name=aliased_tag_autocomplete)
    async def view(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> None:
        """Display a tag by name through the 'tag view' subcommand.

        This behaves identically to the base 'tag get' command.

        Args:
            ctx: Invocation context.
            name: Tag name to retrieve.
        """
        await self._tag_get(ctx, name=name)

    @tag.command(aliases=["add"])
    @commands.guild_only()
    @app_commands.describe(name="The tag name", content="The tag content")
    async def create(
        self,
        ctx: GenjiCtx,
        name: Annotated[str, TagName],
        *,
        content: Annotated[str, commands.clean_content],
    ) -> Message | None:
        """Create a new tag owned by the invoking user.

        Validates tag name and content length, then sends a creation
        request to the API.

        Args:
            ctx: Invocation context.
            name: Tag name to create.
            content: Tag content text.
        """
        if self.is_tag_being_made(ctx.guild.id, name):  # pyright: ignore[reportOptionalMemberAccess]
            return await ctx.send("This tag is currently being made by someone.")
        if len(content) > 2000:  # noqa: PLR2004
            return await ctx.send("Tag content is a maximum of 2000 characters.")
        await self.create_tag(ctx, name, content)

    @tag.command()
    @commands.guild_only()
    @app_commands.rename(new_name="aliased-name", old_name="original-tag")
    @app_commands.describe(new_name="The name of the alias", old_name="The original tag to alias")
    @app_commands.autocomplete(old_name=non_aliased_tag_autocomplete)
    async def alias(
        self,
        ctx: GenjiCtx,
        new_name: Annotated[str, TagName],
        *,
        old_name: Annotated[str, TagName],
    ) -> None:
        """Create an alias for an existing tag.

        Registers a new alias name pointing to an existing tag owned by the user.

        Args:
            ctx: Invocation context.
            new_name: The new alias name.
            old_name: The original tag to alias.
        """
        op = OpAlias(guild_id=ctx.guild.id, new_name=new_name, old_name=old_name, owner_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        res = await self._api_mutate(op)
        r = res.results[0]
        if not r.ok:
            await ctx.send(r.message or "Failed to create alias.")
        elif (r.affected or 0) == 0:
            await ctx.send(f'A tag with the name of "{old_name}" does not exist.')
        else:
            await ctx.send(f'Tag alias "{new_name}" that points to "{old_name}" successfully created.')

    @tag.command(ignore_extra=False)
    @commands.guild_only()
    async def make(self, ctx: GenjiCtx) -> Message | None:  # noqa: PLR0911, PLR0912
        """Interactively create a new tag via text or modal input.

        If invoked via a slash command interaction, a modal is displayed.
        Otherwise, this initiates a text-based interactive tag creation flow.

        Args:
            ctx: Invocation context.
        """
        if ctx.interaction is not None:
            modal = TagMakeModal(self, ctx)
            await ctx.interaction.response.send_modal(modal)
            return

        await ctx.send("Hello. What would you like the tag's name to be?")
        converter = TagName()
        original = ctx.message

        def check(msg) -> bool:  # noqa: ANN001
            return msg.author == ctx.author and msg.channel == ctx.channel

        try:
            name_msg = await self.bot.wait_for("message", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("You took long. Goodbye.")

        try:
            ctx.message = name_msg
            name = await converter.convert(ctx, name_msg.content)
        except commands.BadArgument as e:
            return await ctx.send(f'{e}. Redo the command "{ctx.prefix}tag make" to retry.')
        finally:
            ctx.message = original

        if self.is_tag_being_made(ctx.guild.id, name):  # pyright: ignore[reportOptionalMemberAccess]
            return await ctx.send(
                f'Sorry. This tag is currently being made by someone. Redo the command "{ctx.prefix}tag make" to retry.'
            )

        exists = await self._api_search(ctx.guild.id, name=name, fuzzy=False, limit=1)  # pyright: ignore[reportOptionalMemberAccess]
        if exists.items:
            return await ctx.send(
                f'Sorry. A tag with that name already exists. Redo the command "{ctx.prefix}tag make" to retry.'
            )

        self.add_in_progress_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]
        await ctx.send(
            f"Neat. So the name is {name}. What about the tag's content? "
            f"**You can type {ctx.prefix}abort to abort the tag make process.**"
        )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=300.0)
        except asyncio.TimeoutError:
            self.remove_in_progress_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]
            return await ctx.send("You took too long. Goodbye.")

        if msg.content == f"{ctx.prefix}abort":
            self.remove_in_progress_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]
            return await ctx.send("Aborting.")
        elif msg.content:
            clean_content = await commands.clean_content().convert(ctx, msg.content)
        else:
            clean_content = msg.content

        if msg.attachments:
            clean_content = f"{clean_content}\n{msg.attachments[0].url}"

        if len(clean_content) > 2000:  # noqa: PLR2004
            return await ctx.send("Tag content is a maximum of 2000 characters.")

        try:
            await self.create_tag(ctx, name, clean_content)
        finally:
            self.remove_in_progress_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]

    @make.error
    async def tag_make_error(self, ctx: GenjiCtx, error: commands.CommandError) -> None:
        """Handle errors for the 'tag make' command.

        Sends a friendly reminder when too many arguments are provided.

        Args:
            ctx: Invocation context.
            error: The raised command error.
        """
        if isinstance(error, commands.TooManyArguments):
            await ctx.send(f"Please call just {ctx.prefix}tag make")

    @tag.command()
    @commands.guild_only()
    @app_commands.describe(
        name="The tag to edit",
        content="The new content of the tag, if not given then a modal is opened",
    )
    @app_commands.autocomplete(name=owned_non_aliased_tag_autocomplete)
    async def edit(
        self,
        ctx: GenjiCtx,
        name: Annotated[str, TagName(lower=True)],
        *,
        content: Annotated[Optional[str], commands.clean_content] = None,
    ) -> Message | None:
        """Edit an existing tag owned by the invoking user.

        If content is omitted and the command is run via a slash interaction,
        a modal is presented to edit the tag interactively.

        Args:
            ctx: Invocation context.
            name: Tag name to modify.
            content: Optional new content to replace the existing tag text.
        """
        if content is None:
            if ctx.interaction is None:
                raise commands.BadArgument("Missing content to edit tag with")
            res = await self._api_search(
                ctx.guild.id,  # pyright: ignore[reportOptionalMemberAccess]
                name=name,
                owner_id=ctx.author.id,
                include_content=True,
                include_aliases=False,
                limit=1,
            )
            if not res.items or not (res.items[0].content):
                await ctx.send(
                    "Could not find a tag with that name, are you sure it exists or you own it?",
                    ephemeral=True,
                )
                return
            modal = TagEditModal(res.items[0].content)
            await ctx.interaction.response.send_modal(modal)
            await modal.wait()
            ctx.interaction = modal.interaction
            content = modal.text

        if len(content) > 2000:  # noqa: PLR2004
            return await ctx.send("Tag content can only be up to 2000 characters")

        op = OpEdit(guild_id=ctx.guild.id, name=name, new_content=content, owner_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        res = await self._api_mutate(op)
        r = res.results[0]
        if not r.ok or (r.affected or 0) == 0:
            await ctx.send("Could not edit that tag. Are you sure it exists and you own it?")
        else:
            await ctx.send("Successfully edited tag.")

    @tag.command(aliases=["delete"])
    @commands.guild_only()
    @app_commands.describe(name="The tag to remove")
    @app_commands.autocomplete(name=owned_aliased_tag_autocomplete)
    async def remove(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> None:
        """Remove a tag (and its aliases) by name.

        Only the owner or authorized users can delete a tag.

        Args:
            ctx: Invocation context.
            name: Tag name to remove.
        """
        op = OpRemove(guild_id=ctx.guild.id, name=name, requester_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        res = await self._api_mutate(op)
        r = res.results[0]
        if not r.ok:
            await ctx.send(r.message or "Could not delete tag. Either it does not exist or you lack permissions.")
            return
        # affected count is not guaranteed; message carries the outcome
        await ctx.send(r.message or "Tag deleted.")

    @tag.command(aliases=["delete_id"])
    @commands.guild_only()
    @app_commands.describe(tag_id="The internal tag ID to delete")
    @app_commands.rename(tag_id="id")
    async def remove_id(self, ctx: GenjiCtx, tag_id: int) -> None:
        """Remove a tag (and its aliases) by internal tag ID.

        Only the owner or authorized users can delete a tag.

        Args:
            ctx: Invocation context.
            tag_id: The tag's internal ID.
        """
        op = OpRemoveById(guild_id=ctx.guild.id, tag_id=tag_id, requester_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        res = await self._api_mutate(op)
        r = res.results[0]
        if not r.ok:
            await ctx.send(r.message or "Could not delete tag. Either it does not exist or you lack permissions.")
            return
        await ctx.send(r.message or "Tag deleted.")

    async def _send_alias_info_embed(self, ctx: GenjiCtx, row: TagRowDTO) -> None:
        """Send an embed displaying information for an alias tag.

        Includes owner and alias metadata.

        Args:
            ctx: Invocation context.
            row: TagRowDTO representing the tag alias data.
        """
        embed = discord.Embed(colour=discord.Colour.blurple(), title=row.name)
        embed.add_field(name="Owner", value=f"<@{row.owner_id}>")
        embed.add_field(name="Alias", value="Yes")
        await ctx.send(embed=embed)

    async def _send_tag_info_embed(self, ctx: GenjiCtx, row: TagRowDTO) -> None:
        """Send an embed displaying information for a regular tag.

        Includes owner, uses, and rank if available.

        Args:
            ctx: Invocation context.
            row: TagRowDTO representing the tag data.
        """
        embed = discord.Embed(colour=discord.Colour.blurple(), title=row.name)
        embed.set_footer(text="Tag info")
        embed.add_field(name="Owner", value=f"<@{row.owner_id}>")
        if row.uses is not None:
            embed.add_field(name="Uses", value=row.uses)
        if row.rank is not None:
            embed.add_field(name="Rank", value=row.rank)
        await ctx.send(embed=embed)

    @tag.command(aliases=["owner"])
    @commands.guild_only()
    @app_commands.describe(name="The tag to retrieve information for")
    @app_commands.autocomplete(name=aliased_tag_autocomplete)
    async def info(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> Message | None:
        """Retrieve detailed information about a tag.

        Displays whether the tag is an alias and its owner and usage stats.

        Args:
            ctx: Invocation context.
            name: Tag name to look up.
        """
        res = await self._api_search(ctx.guild.id, name=name, include_rank=True, include_aliases=True, limit=1)  # pyright: ignore[reportOptionalMemberAccess]
        if not res.items:
            return await ctx.send("Tag not found.")
        row = res.items[0]
        if row.is_alias:
            await self._send_alias_info_embed(ctx, row)
        else:
            await self._send_tag_info_embed(ctx, row)

    @tag.command()
    @commands.guild_only()
    @app_commands.describe(name="The tag to retrieve raw content for")
    @app_commands.autocomplete(name=non_aliased_tag_autocomplete)
    async def raw(self, ctx: GenjiCtx, *, name: Annotated[str, TagName(lower=True)]) -> Message | None:
        """Display the raw, markdown-escaped content of a tag.

        If the content exceeds Discord's message limit, it is uploaded
        as a text file attachment instead.

        Args:
            ctx: Invocation context.
            name: Tag name to fetch.
        """
        try:
            tag = await self.get_tag(ctx.guild.id, name)  # pyright: ignore[reportOptionalMemberAccess]
        except RuntimeError as e:
            return await ctx.send(str(e))
        first_step = discord.utils.escape_markdown(tag["content"])

        content = first_step.replace("<", "\\<")
        if len(content) > 2000:  # noqa: PLR2004
            fp = io.BytesIO(content.encode())
            return await ctx.send(file=discord.File(fp, filename="message_too_long.txt"))
        else:
            return await ctx.send(content)

        await ctx.safe_send(first_step.replace("<", "\\<"), escape_mentions=False)

    @tag.command(name="list")
    @commands.guild_only()
    @app_commands.describe(member="The member to list tags of, if not given then it shows yours")
    async def _list(self, ctx: GenjiCtx, *, member: discord.User = commands.Author) -> None:
        """List all tags owned by a user.

        If no member is specified, lists tags belonging to the invoking user.

        Args:
            ctx: Invocation context.
            member: The user whose tags to list, defaults to the command author.
        """
        res = await self._api_search(ctx.guild.id, owner_id=member.id, include_aliases=True, limit=1000, sort_by="name")  # pyright: ignore[reportOptionalMemberAccess]
        rows = res.items
        if rows:
            entries: list[TagEntry] = [{"id": r.id, "name": r.name, "content": ""} for r in rows]
            p = TagPages(entries=entries, ctx=ctx)
            p.embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
            await p.start()
        else:
            await ctx.send(f"{member} has no tags.")

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.describe(member="The member to list tags of, if not given then it shows yours")
    async def tags(self, ctx: GenjiCtx, *, member: discord.User = commands.Author) -> None:
        """Alias for the 'tag list' command.

        Provides identical behavior under the 'tags' command name.

        Args:
            ctx: Invocation context.
            member: The user whose tags to list, defaults to the command author.
        """
        await ctx.invoke(self._list, member=member)

    async def _tag_all_text_mode(self, ctx: GenjiCtx) -> Message | None:
        """List all tags in the current server in plain text format.

        Used internally when the '--text' flag is passed to 'tag all'.

        Args:
            ctx: Invocation context.
        """
        res = await self._api_search(ctx.guild.id, include_aliases=True, limit=1000, sort_by="uses", sort_dir="desc")  # pyright: ignore[reportOptionalMemberAccess]
        rows = res.items
        if not rows:
            return await ctx.send("This server has no server-specific tags.")
        table = TabularData()
        table.set_columns(["id", "name", "owner_id", "uses", "is_alias"])
        table.add_rows([[r.id, r.name, r.owner_id, r.uses or 0, r.is_alias] for r in rows])
        fp = io.BytesIO(table.render().encode("utf-8"))
        await ctx.send(file=discord.File(fp, "tags.txt"))

    @tag.command(name="all", usage="[text: yes|no]")
    @commands.guild_only()
    async def _all(self, ctx: GenjiCtx, *, flags: TagAllFlags) -> Message | None:
        """List all server-specific tags for the current guild.

        Supports a '--text' flag to export as a text file instead of paginated embeds.

        Args:
            ctx: Invocation context.
            flags: Parsed flag set determining output mode (text or embed).
        """
        if flags.text:
            return await self._tag_all_text_mode(ctx)
        res = await self._api_search(ctx.guild.id, include_aliases=True, limit=1000, sort_by="name")  # pyright: ignore[reportOptionalMemberAccess]
        rows = res.items
        if rows:
            entries: list[TagEntry] = [{"id": r.id, "name": r.name, "content": ""} for r in rows]
            p = TagPages(entries=entries, per_page=20, ctx=ctx)
            await p.start()
        else:
            await ctx.send("This server has no server-specific tags.")

    @tag.command()
    @commands.guild_only()
    @has_guild_permissions(manage_messages=True)
    @app_commands.describe(member="The member to remove all tags of")
    async def purge(self, ctx: GenjiCtx, member: discord.User) -> Message | None:
        """Remove all tags belonging to a given member in the server.

        Requires the 'manage_messages' permission to execute.
        Prompts for confirmation before proceeding with deletion.

        Args:
            ctx: Invocation context.
            member: The Discord user whose tags will be purged.
        """
        # count first for UX
        res = await self._api_search(ctx.guild.id, owner_id=member.id, include_aliases=True, limit=1)  # pyright: ignore[reportOptionalMemberAccess]
        count = res.total or 0 if res.items else 0  # total may be None; fall back to len(items)
        if count == 0:
            return await ctx.send(f"{member} does not have any tags to purge.")

        view = ConfirmationView(f"This will delete {count} tags are you sure? **This action cannot be reversed**.")
        await ctx.send(view=view, ephemeral=True)
        await view.wait()

        if not view.confirmed:
            return await ctx.send("Cancelling tag purge request.")
        op = OpPurge(guild_id=ctx.guild.id, owner_id=member.id, requester_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        out = await self._api_mutate(op)
        r = out.results[0]
        await ctx.send(r.message or f"Successfully removed all tags that belong to {member}.")

    @tag.command()
    @commands.guild_only()
    @app_commands.describe(query="The tag name to search for")
    async def search(self, ctx: GenjiCtx, *, query: Annotated[str, commands.clean_content]) -> Message | None:
        """Search for tags in the current server using fuzzy matching.

        Performs case-insensitive search and displays paginated results.
        Requires a minimum query length of 3 characters.

        Args:
            ctx: Invocation context.
            query: The search string to match tag names against.
        """
        if len(query) < 3:  # noqa: PLR2004
            return await ctx.send("The query length must be at least three characters.")
        res = await self._api_search(ctx.guild.id, name=query, fuzzy=True, include_aliases=True, limit=100)  # pyright: ignore[reportOptionalMemberAccess]
        if res.items:
            entries: list[TagEntry] = [{"id": r.id, "name": r.name, "content": ""} for r in res.items]
            p = TagPages(entries=entries, per_page=20, ctx=ctx)
            await p.start()
        else:
            await ctx.send("No tags found.")

    @tag.command()
    @commands.guild_only()
    @app_commands.describe(tag="The tag to claim")
    @app_commands.autocomplete(tag=aliased_tag_autocomplete)
    async def claim(self, ctx: GenjiCtx, *, tag: Annotated[str, TagName]) -> Message | None:
        """Claim ownership of an unclaimed tag.

        Used when the original tag owner has left the server.
        The requester becomes the new owner if the tag is eligible.

        Args:
            ctx: Invocation context.
            tag: Tag name to claim.
        """
        # UX roughly mirrors original: ensure tag exists
        exists = await self._api_search(ctx.guild.id, name=tag, include_aliases=True, limit=1)  # pyright: ignore[reportOptionalMemberAccess]
        if not exists.items:
            return await ctx.send(f'A tag with the name of "{tag}" does not exist.')
        op = OpClaim(guild_id=ctx.guild.id, name=tag, requester_id=ctx.author.id)  # pyright: ignore[reportOptionalMemberAccess]
        out = await self._api_mutate(op)
        r = out.results[0]
        await ctx.send(r.message or "Tag claimed.")

    @tag.command()
    @commands.guild_only()
    @app_commands.describe(member="The member to transfer the tag to")
    @app_commands.autocomplete(tag=aliased_tag_autocomplete)
    async def transfer(self, ctx: GenjiCtx, member: discord.Member, *, tag: Annotated[str, TagName]) -> Message | None:
        """Transfer a tag to another member.

        Only the current owner may transfer ownership. The recipient cannot be a bot.

        Args:
            ctx: Invocation context.
            member: The new owner to transfer the tag to.
            tag: The tag name to transfer.
        """
        if member.bot:
            return await ctx.send("You cannot transfer a tag to a bot.")
        op = OpTransfer(
            guild_id=ctx.guild.id,  # pyright: ignore[reportOptionalMemberAccess]
            name=tag,
            new_owner_id=member.id,
            requester_id=ctx.author.id,
        )
        out = await self._api_mutate(op)
        r = out.results[0]
        if not r.ok:
            await ctx.send(r.message or f'A tag with the name of "{tag}" does not exist or is not owned by you.')
        else:
            await ctx.send(f"Successfully transferred tag ownership to {member}.")

    @tag.command(hidden=True, with_app_command=False)
    async def config(self, ctx: GenjiCtx) -> None:
        """Reserved command placeholder for future configuration support.

        Currently does nothing besides displaying a stub message.

        Args:
            ctx: Invocation context.
        """

    @tag.command()
    async def random(self, ctx: GenjiCtx) -> Message | None:
        """Display a random tag from the current server.

        Fetches one random tag from the database and displays its name and content.
        If the server has no tags, an informative message is shown.

        Args:
            ctx: Invocation context.
        """
        tag = await self.get_random_tag(ctx.guild)  # pyright: ignore[reportArgumentType]
        if tag is None:
            return await ctx.send("This server has no tags.")
        await ctx.send(f"Random tag found: {tag['name']}\n{tag['content']}")


class TabularData:
    def __init__(self) -> None:
        """Initialize an empty table for ASCII-style tabular data rendering."""
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]) -> None:
        """Set the header columns for the table and initialize column widths.

        Args:
            columns: List of column header strings.
        """
        self._columns = columns
        self._widths = [len(c) + 2 for c in columns]

    def add_row(self, row: Iterable[Any]) -> None:
        """Add a single row of data to the table.

        Automatically adjusts column widths to fit the new row.

        Args:
            row: Iterable of cell values for the new row.
        """
        rows = [str(r) for r in row]
        self._rows.append(rows)
        for index, element in enumerate(rows):
            width = len(element) + 2
            self._widths[index] = max(width, self._widths[index])

    def add_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        """Add multiple rows of data to the table sequentially.

        Args:
            rows: Iterable of row iterables, each representing one table row.
        """
        for row in rows:
            self.add_row(row)

    def render(self) -> str:
        """Render the table as a formatted ASCII string.

        Draws borders, headers, and rows with aligned columns.

        Returns:
            A string representing the rendered table.
        """
        sep = "+".join("-" * w for w in self._widths)
        sep = f"+{sep}+"
        to_draw = [sep]

        def get_entry(d: Sequence) -> str:
            elem = "|".join(f"{e:^{self._widths[i]}}" for i, e in enumerate(d))
            return f"|{elem}|"

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)
        for row in self._rows:
            to_draw.append(get_entry(row))
        to_draw.append(sep)
        return "\n".join(to_draw)
