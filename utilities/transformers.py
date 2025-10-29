from __future__ import annotations

import re
from logging import getLogger
from typing import TYPE_CHECKING

from discord import app_commands

from utilities.errors import UserFacingError
from utilities.extra import time_convert
from utilities.maps import CODE_VERIFICATION

if TYPE_CHECKING:
    from genjipk_sdk.utilities._types import Mechanics, OverwatchMap, Restrictions

    from ._types import GenjiItx

log = getLogger(__name__)


class MapNameTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> OverwatchMap:
        """Transform a string into an OverwatchMap.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input string from the user.

        Returns:
            OverwatchMap: The resolved map object.
        """
        res = await itx.client.api.transform_map_name(value)
        return res

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete map names based on current input.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The partial string input.

        Returns:
            list[app_commands.Choice[str]]: Suggested map name choices.
        """
        names = await itx.client.api.get_autocomplete_map_names(current)
        return [app_commands.Choice(name=name, value=name) for name in names]


class MechanicsTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> Mechanics:
        """Transform a string into a Mechanics value.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input string.

        Returns:
            Mechanics: The parsed mechanics object.
        """
        res = await itx.client.api.transform_map_mechanics(value)
        return res

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete mechanics based on partial input.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The partial input string.

        Returns:
            list[app_commands.Choice[str]]: Suggested mechanic name choices.
        """
        names = await itx.client.api.get_autocomplete_map_mechanics(current)
        return [app_commands.Choice(name=name, value=name) for name in names]


class RestrictionsTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> Restrictions:
        """Transform a string into a Restrictions value.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input string.

        Returns:
            Restrictions: The parsed restrictions object.
        """
        res = await itx.client.api.transform_map_restrictions(value)
        return res

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete restrictions based on partial input.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The partial input string.

        Returns:
            list[app_commands.Choice[str]]: Suggested restriction name choices.
        """
        names = await itx.client.api.get_autocomplete_map_restrictions(current)
        return [app_commands.Choice(name=name, value=name) for name in names]


class _CodeBaseTransformer(app_commands.Transformer):
    @staticmethod
    def _clean_code(map_code: str) -> str:
        """Clean and normalize a user-submitted map code.

        Args:
            map_code (str): The raw code string.

        Returns:
            str: The normalized code (uppercase, spaces trimmed, O->0).
        """
        return map_code.upper().replace("O", "0").lstrip().rstrip()


class CodeSubmissionTransformer(_CodeBaseTransformer):
    async def transform(self, itx: GenjiItx, value: str) -> str:
        """Transform and validate a new submission code.

        Ensures the code format is valid and not already in use.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input code.

        Returns:
            str: The normalized and validated map code.

        Raises:
            UserFacingError: If the code format is invalid or already exists.
        """
        value = self._clean_code(value)
        if not re.match(CODE_VERIFICATION, value):
            raise UserFacingError("Code has an invalid format.")

        if await itx.client.api.map_exists(value):
            raise UserFacingError("Code already exists.")
        return value


class CodeVisibleTransformer(_CodeBaseTransformer):
    async def transform(self, itx: GenjiItx, value: str) -> str:
        """Transform and validate a visible map code.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input code.

        Returns:
            str: The validated code.

        Raises:
            UserFacingError: If the code format is invalid or no maps found.
        """
        value = self._clean_code(value)
        if not re.match(CODE_VERIFICATION, value):
            raise UserFacingError("Code has an invalid format.")

        res = await itx.client.api.transform_map_codes(value, hidden=False, archived=False)
        if not res:
            raise UserFacingError("No maps found.")
        return value

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete visible map codes.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The user's partial input.

        Returns:
            list[app_commands.Choice[str]]: Suggested visible codes.
        """
        codes = await itx.client.api.get_autocomplete_map_codes(current, hidden=False, archived=False)
        return [app_commands.Choice(name=c, value=c) for c in codes]


class CodeAllTransformer(_CodeBaseTransformer):
    async def transform(self, itx: GenjiItx, value: str) -> str:
        """Transform and validate a map code, including hidden or archived maps.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input code.

        Returns:
            str: The validated code.

        Raises:
            UserFacingError: If the code format is invalid or not found.
        """
        value = self._clean_code(value)
        if not re.match(CODE_VERIFICATION, value):
            raise UserFacingError("Code has an invalid format.")

        res = await itx.client.api.transform_map_codes(value)

        if not res:
            raise UserFacingError("No maps found.")
        return value

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete any map code.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The user's partial input.

        Returns:
            list[app_commands.Choice[str]]: Suggested codes.
        """
        codes = await itx.client.api.get_autocomplete_map_codes(current)
        return [app_commands.Choice(name=c, value=c) for c in codes]


class UserTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> int:
        """Transform a string into a user ID.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input value, expected to be a user ID.

        Returns:
            int: The user ID.

        Raises:
            ValueError: If the value is not a valid digit.
        """
        if value.isdigit():
            return int(value)
        raise ValueError("This shouldn't happen?")

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete user display names or IDs.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The partial user input.

        Returns:
            list[app_commands.Choice[str]]: Suggested users.
        """
        users = await itx.client.api.get_autocomplete_users(current)
        return [app_commands.Choice(name=names[:100], value=str(user_id)) for user_id, names in users]


class FakeUserTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> int:
        """Transform a string into a user ID.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input value, expected to be a user ID.

        Returns:
            int: The user ID.

        Raises:
            ValueError: If the value is not a valid digit.
        """
        if value.isdigit():
            return int(value)
        raise ValueError("This shouldn't happen?")

    async def autocomplete(self, itx: GenjiItx, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete user display names or IDs.

        Args:
            itx (GenjiItx): The interaction context.
            current (str): The partial user input.

        Returns:
            list[app_commands.Choice[str]]: Suggested users.
        """
        users = await itx.client.api.get_autocomplete_users(current, fake_users_only=True)
        return [app_commands.Choice(name=names[:100], value=str(user_id)) for user_id, names in users]


class RecordTransformer(app_commands.Transformer):
    async def transform(self, itx: GenjiItx, value: str) -> float:
        """Transform a string into a float time value for record comparison.

        Args:
            itx (GenjiItx): The interaction context.
            value (str): The input time string.

        Returns:
            float: The parsed time in seconds.

        Raises:
            UserFacingError: If the input format is invalid.
        """
        try:
            return time_convert(value)
        except ValueError:
            raise UserFacingError("Medal input is in an incorrect format.")
