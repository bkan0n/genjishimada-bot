from __future__ import annotations

import asyncio
import datetime
import mimetypes
import os
from functools import lru_cache
from http import HTTPStatus
from io import BytesIO
from logging import getLogger
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Coroutine,
    Literal,
    Mapping,
    Type,
    TypeVar,
    overload,
)
from urllib.parse import quote
from uuid import UUID

import aiohttp
import discord
import msgspec
from genjipk_sdk.models import (
    NOTIFICATION_TYPES,
    ChangeRequestCreateDTO,
    CompletionCreateDTO,
    CompletionPatchDTO,
    Guide,
    LogCreateDTO,
    LootboxKeyType,
    MapMasteryCreateDTO,
    MapMasteryCreateReturnDTO,
    MapMasteryData,
    MapPatchDTO,
    MapReadPartialDTO,
    NewsfeedEvent,
    Notification,
    OverwatchUsernamesReadDTO,
    OverwatchUsernamesUpdate,
    PlaytestAssociateIDThread,
    PlaytestPatchDTO,
    PlaytestVote,
    PlaytestVotesAll,
    QualityValueDTO,
    TierChange,
    UserCreateDTO,
    UserReadDTO,
    UserUpdateDTO,
    XpGrant,
    XpGrantResult,
)
from genjipk_sdk.models.completions import (
    CompletionVerificationPutDTO,
    PendingVerification,
    QualityUpdateDTO,
    SuspiciousCompletionWriteDTO,
    UpvoteCreateDTO,
)
from genjipk_sdk.models.jobs import (
    CreateMapReturnDTO,
    CreatePublishNewsfeedReturnDTO,
    JobStatus,
    JobUpdate,
    SubmitCompletionReturnDTO,
    UpvoteSubmissionReturnDTO,
)
from genjipk_sdk.models.maps import PlaytestReadDTO
from genjipk_sdk.models.tags import (
    TagsAutocompleteRequest,
    TagsAutocompleteResponse,
    TagsMutateRequest,
    TagsMutateResponse,
    TagsSearchFilters,
    TagsSearchResponse,
)
from genjipk_sdk.models.users import RankDetailReadDTO
from genjipk_sdk.utilities import DifficultyAll
from genjipk_sdk.utilities._types import (
    GuideURL,
    Mechanics,
    NewsfeedEventType,
    OverwatchCode,
    OverwatchMap,
    Restrictions,
)
from multidict import MultiDict

from extensions.completions import CompletionLeaderboardFormattable, CompletionUserFormattable
from utilities.change_requests import FormattableChangeRequest, FormattableStaleChangeRequest
from utilities.completions import CompletionSubmissionModel, SuspiciousCompletionModel
from utilities.errors import APIUnavailableError
from utilities.maps import MapCreateModel, MapModel
from utilities.views.mod_guides_view import FormattableGuide

if TYPE_CHECKING:
    from genjipk_sdk.utilities import DifficultyTop
    from genjipk_sdk.utilities._types import (
        MapCategory,
        PlaytestStatus,
    )

    import core

    T = TypeVar("T")
    D = TypeVar("D")
    Response = Coroutine[Any, Any, T]

_BASE = (
    "http://genjishimada-api-dev:8000"
    if os.getenv("BOT_ENVIRONMENT") == "development"
    else "http://genjishimada-api:8000"
)

log = getLogger(__name__)

_TriFilter = Literal["All", "With", "Without"]
CompletionFilter = _TriFilter
MedalFilter = _TriFilter
PlaytestFilter = _TriFilter


@lru_cache(maxsize=None)
def get_decoder(model: type[D]) -> msgspec.json.Decoder[D]:
    """Return a cached msgspec decoder for the given model type.

    Args:
        model (type[D]): The msgspec Struct model to decode.

    Returns:
        msgspec.json.Decoder[D]: A decoder for the model.
    """
    return msgspec.json.Decoder(model)


class Route:
    BASE: ClassVar[str] = _BASE + "/api/v3"

    def __init__(self, method: str, path: str, **parameters: Any) -> None:  # noqa: ANN401
        """Initialize a Route with method, path, and optional parameters.

        Args:
            method (str): HTTP method (e.g., "GET", "POST").
            path (str): The API route path.
            **parameters (Any): Optional parameters to format into the path.
        """
        self.path: str = path
        self.method: str = method
        url = self.BASE + self.path
        if parameters:
            url = url.format_map({k: quote(v, safe="") if isinstance(v, str) else v for k, v in parameters.items()})
        self.url: str = url


class APIClient:
    """Represents an HTTP client sending HTTP requests to the Genji Shimada API."""

    def __init__(self) -> None:
        """Initialize the APIClient with authentication and heartbeat logic."""
        self.api_key: str = os.getenv("API_KEY", "")
        self._encoder = msgspec.json.Encoder(decimal_format="number")
        self.__session: aiohttp.ClientSession = aiohttp.ClientSession(headers={"X-API-KEY": self.api_key})
        self._is_available = False
        self._lock = asyncio.Lock()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Continuously ping the API to determine availability and update internal state."""
        interval = 30
        while True:
            try:
                async with self._lock:
                    retry_after = await self._check_availability()
                    if retry_after is not None:
                        interval = retry_after
            except Exception:
                self._is_available = False
                log.info("Genji Shimada APIClient Heartbeat blocked.")
            await asyncio.sleep(interval)

    async def _check_availability(self) -> int | None:
        """Send a health check to the API and determine next heartbeat interval.

        Returns:
            int | None: Number of seconds to wait before the next check, or None for default.
        """
        try:
            async with self.__session.get(f"{_BASE}/healthcheck") as resp:
                if resp.status == HTTPStatus.OK:
                    self._is_available = True
                    return None
                self._is_available = False
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        return int(ra)
                    except ValueError:
                        log.warning("Invalid Retry-After header: %r", ra)
                return None
        except Exception:
            self._is_available = False
            return None

    async def _ensure_available(self) -> None:
        """Ensure the API is available before processing a request.

        Raises:
            APIUnavailableError: If the API is currently unavailable.
        """
        if not self._is_available:
            raise APIUnavailableError("API is currently unavailable.")

    def _to_form_str(self, v: Any) -> str:  # noqa: ANN401
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    async def _request_multipart(  # noqa: PLR0913
        self,
        route: Route,
        *,
        response_model: Type[T] | T | None = None,
        file: bytes,
        file_field: str = "data",
        filename: str = "screenshot.png",
        content_type: str = "application/octet-stream",
    ) -> Any:  # noqa: ANN401
        """Send a multipart/form-data request with a single file and optional fields.

        Builds a multipart form, attaching discrete form fields and one file
        upload, and dispatches it to the given API route. Decodes the response
        into the requested type if a response model is provided.

        Args:
            route: The API route definition containing method and URL.
            response_model: Optional type or instance used to decode the response.
                If None, returns raw bytes. If `str` and response is bytes,
                decodes to a string.
            file: Raw bytes for the file to upload.
            file_field: Form field name for the file part. Defaults to `"data"`.
            filename: Name of the uploaded file. Defaults to `"screenshot.png"`.
            content_type: MIME type of the uploaded file. Defaults to
                `"application/octet-stream"`.

        Returns:
            Any: Decoded response, raw bytes, string, or None depending on
            `response_model` and API response.

        Raises:
            ValueError: If a response model is expected but no content is returned.
            APIUnavailableError: If a connection error occurs and the API is
                marked unavailable.
        """
        await self._ensure_available()

        headers = {"X-API-KEY": self.api_key}
        form = aiohttp.FormData()
        form.add_field(
            name=file_field,
            value=file,
            filename=filename,
            content_type=content_type,
        )
        try:
            async with self.__session.request(route.method, route.url, headers=headers, data=form) as resp:
                resp.raise_for_status()

                if resp.status == HTTPStatus.NO_CONTENT:
                    if response_model:
                        raise ValueError(f"Expected JSON but got no content from {route.url}")
                    return None

                raw = await resp.read()
                if response_model is None:
                    return raw
                if raw is None:
                    return None
                if response_model is str and isinstance(raw, bytes):
                    return raw.decode()
                return get_decoder(response_model).decode(raw)

        except aiohttp.ClientConnectorError:
            self._is_available = False
            raise APIUnavailableError("Connection error; API marked unavailable.")

    async def _request(
        self,
        route: Route,
        *,
        response_model: Type[T] | T | None = None,
        data: msgspec.Struct | None = None,
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Send an HTTP request to the API and optionally decode the response.

        Args:
            route (Route): The route to call.
            response_model (Type[T] | T | None): Optional type to decode the response as.
            data (msgspec.Struct | None): Optional body data to encode as JSON.
            params (Mapping[str, Any] | None): Optional query parameters.
            **kwargs (Any): Additional aiohttp request parameters.

        Returns:
            Any: Decoded response, raw bytes, or None depending on inputs.

        Raises:
            APIUnavailableError: If the API connection fails.
            ValueError: If no content is returned but a model was expected.
        """
        await self._ensure_available()
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        if data is not None:
            kwargs["data"] = self._encoder.encode(data)

        if params:
            flat_params = MultiDict()
            for k, v in params.items():
                if v is None:
                    continue
                elif isinstance(v, list):
                    for item in v:
                        flat_params.add(k, str(item))
                else:
                    flat_params.add(k, str(v))
            params = flat_params

        try:
            async with self.__session.request(
                route.method, route.url, headers=headers, params=params, **kwargs
            ) as resp:
                resp.raise_for_status()
                if resp.status == HTTPStatus.NO_CONTENT:
                    if response_model:
                        raise ValueError(f"Expected JSON but got no content from {route.url}")
                    return None

                raw = await resp.read()
                if response_model is None:
                    return raw
                if raw is None:
                    return None
                return get_decoder(response_model).decode(raw)

        except aiohttp.ClientConnectorError:
            self._is_available = False
            raise APIUnavailableError("Connection error; API marked unavailable.")

    def submit_map(self, data: MapCreateModel) -> Response[CreateMapReturnDTO]:
        """Submit a new map to the API.

        Args:
            data (MapCreateModel): The map data to create.

        Returns:
            Response[MapCreateModel]: The created map.
        """
        r = Route("POST", "/maps")
        return self._request(r, response_model=CreateMapReturnDTO, data=data)

    def get_maps(  # noqa: PLR0913
        self,
        playtesting: PlaytestStatus | None = None,
        archived: bool | None = None,
        hidden: bool | None = None,
        official: bool | None = None,
        playtest_thread_id: int | None = None,
        code: OverwatchCode | None = None,
        category: list[MapCategory] | None = None,
        map_name: list[OverwatchMap] | None = None,
        creator_ids: list[int] | None = None,
        creator_names: list[str] | None = None,
        mechanics: list[Mechanics] | None = None,
        restrictions: list[Restrictions] | None = None,
        difficulty_exact: DifficultyTop | None = None,
        difficulty_range_min: DifficultyAll | None = None,
        difficulty_range_max: DifficultyAll | None = None,
        minimum_quality: int | None = None,
        user_id: int | None = None,
        medal_filter: MedalFilter = "All",
        completion_filter: CompletionFilter = "All",
        playtest_filter: PlaytestFilter = "All",
        return_all: bool = True,
        page_size: Literal[10, 20, 25, 50] = 10,
        page_number: int = 1,
    ) -> Response[list[MapModel]]:
        """Get a list of maps matching the given filters.

        Args:
            playtesting (PlaytestStatus | None): Filter by playtesting status.
            archived (bool | None): Filter by archived state.
            hidden (bool | None): Filter by hidden state.
            official (bool | None): Filter by official tag.
            playtest_thread_id (int | None): Match by playtest thread ID.
            code (OverwatchCode | None): Filter by map code.
            category (list[MapCategory] | None): Filter by map categories.
            map_name (list[OverwatchMap] | None): Filter by map names.
            creator_ids (list[int] | None): Filter by creator user IDs.
            creator_names (list[str] | None): Filter by creator display names.
            mechanics (list[Mechanics] | None): Filter by mechanics.
            restrictions (list[Restrictions] | None): Filter by restrictions.
            difficulty_exact (DifficultyTop | None): Match exact difficulty.
            difficulty_range_min (DifficultyAll | None): Minimum difficulty bound.
            difficulty_range_max (DifficultyAll | None): Maximum difficulty bound.
            minimum_quality (int | None): Minimum required quality score.
            user_id (int | None): Filter by associated user ID.
            medal_filter (MedalFilter): Medal tri-filter.
            completion_filter (CompletionFilter): Completion tri-filter.
            playtest_filter (PlaytestFilter): Playtest tri-filter.
            return_all (bool): Whether to fetch all pages (default: True).
            page_size (Literal[10, 20, 25, 50]): Page size.
            page_number (int): Page number.

        Returns:
            Response[list[MapModel]]: A list of maps matching the filters.
        """
        r = Route("GET", "/maps")
        params = {
            "playtest_status": playtesting,
            "archived": archived,
            "hidden": hidden,
            "official": official,
            "playtest_thread_id": playtest_thread_id,
            "code": code,
            "category": category,
            "map_name": map_name,
            "creator_ids": creator_ids,
            "creator_names": creator_names,
            "mechanics": mechanics,
            "restrictions": restrictions,
            "difficulty_exact": difficulty_exact,
            "difficulty_range_min": difficulty_range_min,
            "difficulty_range_max": difficulty_range_max,
            "minimum_quality": minimum_quality,
            "user_id": user_id,
            "medal_filter": medal_filter,
            "completion_filter": completion_filter,
            "playtest_filter": playtest_filter,
            "page_size": page_size,
            "page_number": page_number,
            "return_all": return_all,
        }
        return self._request(r, response_model=list[MapModel], params=params)

    async def get_map(  # noqa: PLR0913
        self,
        playtesting: PlaytestStatus | None = None,
        archived: bool | None = None,
        hidden: bool | None = None,
        official: bool | None = None,
        playtest_thread_id: int | None = None,
        code: OverwatchCode | None = None,
        category: list[MapCategory] | None = None,
        map_name: list[OverwatchMap] | None = None,
        creator_ids: list[int] | None = None,
        creator_names: list[str] | None = None,
        mechanics: list[Mechanics] | None = None,
        restrictions: list[Restrictions] | None = None,
        difficulty_exact: DifficultyTop | None = None,
        difficulty_range_min: DifficultyAll | None = None,
        difficulty_range_max: DifficultyAll | None = None,
        minimum_quality: int | None = None,
        user_id: int | None = None,
        medal_filter: MedalFilter = "All",
        completion_filter: CompletionFilter = "All",
        playtest_filter: PlaytestFilter = "All",
        return_all: bool = True,
        page_size: Literal[10, 20, 25, 50] = 10,
        page_number: int = 1,
    ) -> MapModel:
        """Get the first map result matching the given filters.

        Args:
            playtesting (PlaytestStatus | None): Filter by playtesting status.
            archived (bool | None): Filter by archived state.
            hidden (bool | None): Filter by hidden state.
            official (bool | None): Filter by official tag.
            playtest_thread_id (int | None): Match by playtest thread ID.
            code (OverwatchCode | None): Filter by map code.
            category (list[MapCategory] | None): Filter by map categories.
            map_name (list[OverwatchMap] | None): Filter by map names.
            creator_ids (list[int] | None): Filter by creator user IDs.
            creator_names (list[str] | None): Filter by creator display names.
            mechanics (list[Mechanics] | None): Filter by mechanics.
            restrictions (list[Restrictions] | None): Filter by restrictions.
            difficulty_exact (DifficultyTop | None): Match exact difficulty.
            difficulty_range_min (DifficultyAll | None): Minimum difficulty bound.
            difficulty_range_max (DifficultyAll | None): Maximum difficulty bound.
            minimum_quality (int | None): Minimum required quality score.
            user_id (int | None): Filter by associated user ID.
            medal_filter (MedalFilter): Medal tri-filter.
            completion_filter (CompletionFilter): Completion tri-filter.
            playtest_filter (PlaytestFilter): Playtest tri-filter.
            return_all (bool): Whether to fetch all pages (default: True).
            page_size (Literal[10, 20, 25, 50]): Page size.
            page_number (int): Page number.

        Returns:
            MapModel: The first map result matching the filters.

        Raises:
            ValueError: If no map is found.
        """
        params = {
            "playtesting": playtesting,
            "archived": archived,
            "hidden": hidden,
            "official": official,
            "playtest_thread_id": playtest_thread_id,
            "code": code,
            "category": category,
            "map_name": map_name,
            "creator_ids": creator_ids,
            "creator_names": creator_names,
            "mechanics": mechanics,
            "restrictions": restrictions,
            "difficulty_exact": difficulty_exact,
            "difficulty_range_min": difficulty_range_min,
            "difficulty_range_max": difficulty_range_max,
            "minimum_quality": minimum_quality,
            "user_id": user_id,
            "medal_filter": medal_filter,
            "completion_filter": completion_filter,
            "playtest_filter": playtest_filter,
            "return_all": return_all,
            "page_size": page_size,
            "page_number": page_number,
        }
        maps = await self.get_maps(**params)
        if maps:
            return maps[0]
        raise ValueError("No maps were found.")

    def get_playtest(self, thread_id: int) -> Response[PlaytestReadDTO]:
        """Retrieve a playtest record by thread ID.

        Args:
            thread_id (int): The ID of the playtest thread.

        Returns:
            Response[PlaytestReadDTO]: The playtest data.
        """
        return self._request(
            Route("GET", "/maps/playtests/{thread_id}", thread_id=thread_id),
            response_model=PlaytestReadDTO,
        )

    def edit_map(self, code: OverwatchCode, data: MapPatchDTO) -> Response[None]:
        """Apply updates to a map by code.

        Args:
            code (OverwatchCode): The map code to update.
            data (MapPatchDTO): The patch data to apply.
        """
        r = Route("PATCH", "/maps/{code}", code=code)
        return self._request(r, data=data)

    def map_exists(self, code: OverwatchCode) -> Response[bool]:
        """Check if a map with the given code exists.

        Args:
            code (OverwatchCode): The map code to check.

        Returns:
            Response[bool]: Whether the map exists.
        """
        r = Route("GET", "/maps/{code}/exists", code=code)
        return self._request(r, response_model=bool)

    def get_guides(self, code: OverwatchCode, include_records: bool = False) -> Response[list[FormattableGuide]]:
        """Retrieve a list of guides associated with a given map code.

        Args:
            code (OverwatchCode): The code of the map to fetch guides for.
            include_records (bool): Whether or not to include record videos.

        Returns:
            Response[list[FormattableGuide]]: List of guides.
        """
        r = Route("GET", "/maps/{code}/guides", code=code)
        return self._request(r, response_model=list[FormattableGuide], params={"include_records": include_records})

    def delete_guide(self, code: OverwatchCode, user_id: int) -> Response[None]:
        """Delete a guide for a specific user and map.

        Args:
            code (OverwatchCode): The map code.
            user_id (int): The ID of the user who submitted the guide.
        """
        r = Route("DELETE", "/maps/{code}/guides/{user_id}", code=code, user_id=user_id)
        return self._request(r)

    def edit_guide(self, code: OverwatchCode, user_id: int, url: GuideURL) -> Response[Guide]:
        """Update a guide's URL for a user on a specific map.

        Args:
            code (OverwatchCode): The map code.
            user_id (int): The user ID associated with the guide.
            url (GuideURL): The new guide URL.

        Returns:
            Response[Guide]: The updated guide.
        """
        r = Route("PATCH", "/maps/{code}/guides/{user_id}", code=code, user_id=user_id)
        return self._request(r, response_model=Guide, params={"url": url})

    def create_guide(self, code: OverwatchCode, data: Guide) -> Response[Guide]:
        """Create a guide for a map.

        Args:
            code (OverwatchCode): The map code to associate with the guide.
            data (Guide): The guide data.

        Returns:
            Response[Guide]: The created guide.
        """
        r = Route("POST", "/maps/{code}/guides", code=code)
        return self._request(r, response_model=Guide, data=data)

    @overload
    async def get_plot_file(self, *, code: OverwatchCode) -> discord.File: ...

    @overload
    async def get_plot_file(self, *, thread_id: int) -> discord.File: ...

    async def get_plot_file(self, *, code: OverwatchCode | None = None, thread_id: int | None = None) -> discord.File:
        """Retrieve the vote distribution plot for a map or playtest.

        Args:
            code (OverwatchCode | None): The map code (optional, if thread_id is used).
            thread_id (int | None): The playtest thread ID (optional, if code is used).

        Returns:
            discord.File: The file containing the vote plot.

        Raises:
            ValueError: If neither code nor thread_id is provided.
        """
        if thread_id is not None:
            url = "/maps/playtests/{thread_id}/plot"
        elif code is not None:
            url = "/maps/{code}/plot"
        else:
            raise ValueError("You must provide either map_id or thread_id")

        r = Route("GET", url, code=code, thread_id=thread_id)
        image_bytes = await self._request(r)
        return discord.File(fp=BytesIO(image_bytes), filename="vote_hist.png")

    def associate_playtest_meta(self, data: PlaytestAssociateIDThread) -> Response[PlaytestReadDTO]:
        """Associate thread metadata with a playtest.

        Args:
            data (PlaytestAssociateIDThread): The data containing thread and map association.

        Returns:
            Response[PlaytestReadDTO]: The updated playtest data.
        """
        return self._request(
            Route("PATCH", "/maps/playtests"),
            data=data,
            response_model=PlaytestReadDTO,
        )

    def get_partial_map(self, code: OverwatchCode) -> Response[MapReadPartialDTO]:
        """Fetch a partial map model used for playtest setup.

        Args:
            code (OverwatchCode): The map code to query.

        Returns:
            Response[MapReadPartialDTO]: Partial map data.
        """
        r = Route("GET", "/maps/{code}/partial", code=code)
        return self._request(r, response_model=MapReadPartialDTO)

    def cast_playtest_vote(self, thread_id: int, user_id: int, vote: PlaytestVote) -> Response[None]:
        """Submit a vote on a playtest thread.

        Args:
            thread_id (int): The playtest thread ID.
            user_id (int): The ID of the user casting the vote.
            vote (PlaytestVote): The vote to cast.
        """
        r = Route("POST", "/maps/playtests/{thread_id}/vote/{user_id}", thread_id=thread_id, user_id=user_id)
        return self._request(r, data=vote)

    def delete_playtest_vote(self, thread_id: int, user_id: int) -> Response[None]:
        """Remove a user's vote on a playtest.

        Args:
            thread_id (int): The playtest thread ID.
            user_id (int): The ID of the user whose vote will be deleted.
        """
        r = Route("DELETE", "/maps/playtests/{thread_id}/vote/{user_id}", thread_id=thread_id, user_id=user_id)
        return self._request(r)

    def delete_all_playtest_votes(self, thread_id: int) -> Response[None]:
        """Remove all votes on a playtest thread.

        Args:
            thread_id (int): The ID of the playtest thread.
        """
        r = Route("DELETE", "/maps/playtests/{thread_id}/vote", thread_id=thread_id)
        return self._request(r)

    def get_all_votes(self, thread_id: int) -> Response[PlaytestVotesAll]:
        """Retrieve all votes for a playtest.

        Args:
            thread_id (int): The playtest thread ID.

        Returns:
            Response[PlaytestVotesAll]: All votes and statistics.
        """
        r = Route("GET", "/maps/playtests/{thread_id}/votes", thread_id=thread_id)
        return self._request(r, response_model=PlaytestVotesAll)

    def edit_playtest_meta(self, thread_id: int, data: PlaytestPatchDTO) -> Response[None]:
        """Patch metadata associated with a playtest thread.

        Args:
            thread_id (int): The ID of the playtest thread.
            data (PlaytestPatchDTO): The metadata changes to apply.
        """
        r = Route(
            "PATCH",
            "/maps/playtests/{thread_id}",
            thread_id=thread_id,
        )
        return self._request(r, data=data)

    def get_autocomplete_map_names(self, search: str, *, limit: int = 5) -> Response[list[OverwatchMap]]:
        """Search for matching map names.

        Args:
            search (str): The input string to match against map names.
            limit (int, optional): Max number of results. Defaults to 5.

        Returns:
            Response[list[OverwatchMap]]: Matching map names.
        """
        r = Route("GET", "/utilities/autocomplete/names")
        params = {"search": search, "limit": limit}
        return self._request(r, params=params, response_model=list[OverwatchMap])

    def transform_map_name(self, search: str) -> Response[OverwatchMap]:
        """Convert a string into a valid OverwatchMap.

        Args:
            search (str): The input string.

        Returns:
            Response[OverwatchMap]: The transformed map.
        """
        r = Route("GET", "/utilities/transformers/names")
        params = {"search": search}
        return self._request(r, params=params, response_model=OverwatchMap)

    def get_autocomplete_map_mechanics(self, search: str, *, limit: int = 5) -> Response[list[Mechanics]]:
        """Search for matching map mechanics.

        Args:
            search (str): The input string to match against mechanics.
            limit (int, optional): Max number of results. Defaults to 5.

        Returns:
            Response[list[Mechanics]]: Matching mechanics.
        """
        r = Route("GET", "/utilities/autocomplete/mechanics")
        params = {"search": search, "limit": limit}
        return self._request(r, params=params, response_model=list[Mechanics])

    def transform_map_mechanics(self, search: str) -> Response[Mechanics]:
        """Convert a string into a Mechanics.

        Args:
            search (str): The input string.

        Returns:
            Response[Mechanics]: The transformed mechanics value.
        """
        r = Route("GET", "/utilities/transformers/mechanics")
        params = {"search": search}
        return self._request(r, params=params, response_model=Mechanics)

    def get_autocomplete_map_restrictions(self, search: str, *, limit: int = 5) -> Response[list[Restrictions]]:
        """Search for matching map restrictions.

        Args:
            search (str): The input string to match against restrictions.
            limit (int, optional): Max number of results. Defaults to 5.

        Returns:
            Response[list[Restrictions]]: Matching restriction values.
        """
        r = Route("GET", "/utilities/autocomplete/restrictions")
        params = {"search": search, "limit": limit}
        return self._request(r, params=params, response_model=list[Restrictions])

    def transform_map_restrictions(self, search: str) -> Response[Restrictions]:
        """Convert a string into a Restrictions.

        Args:
            search (str): The input string.

        Returns:
            Response[Restrictions]: The transformed restriction value.
        """
        r = Route("GET", "/utilities/transformers/restrictions")
        params = {"search": search}
        return self._request(r, params=params, response_model=Restrictions)

    def get_autocomplete_map_codes(
        self,
        search: str,
        *,
        limit: int = 5,
        archived: bool | None = None,
        hidden: bool | None = None,
        playtesting: PlaytestStatus | None = None,
    ) -> Response[list[OverwatchCode]]:
        """Search for map codes matching the input string.

        Args:
            search (str): The code fragment to search for.
            limit (int, optional): Maximum results to return. Defaults to 5.
            archived (bool | None): Optional archived filter.
            hidden (bool | None): Optional hidden filter.
            playtesting (PlaytestStatus | None): Optional playtesting filter.

        Returns:
            Response[list[OverwatchCode]]: Matching codes.
        """
        r = Route("GET", "/utilities/autocomplete/codes")
        params = {"search": search, "limit": limit, "archived": archived, "hidden": hidden, "playtesting": playtesting}
        return self._request(r, params=params, response_model=list[OverwatchCode])

    def transform_map_codes(
        self,
        search: str,
        *,
        archived: bool | None = None,
        hidden: bool | None = None,
        playtesting: PlaytestStatus | None = None,
    ) -> Response[OverwatchCode]:
        """Convert a string into an OverwatchCode.

        Args:
            search (str): The input string.
            archived (bool | None): Optional archived filter.
            hidden (bool | None): Optional hidden filter.
            playtesting (PlaytestStatus | None): Optional playtesting filter.

        Returns:
            Response[OverwatchCode]: The transformed map code.
        """
        r = Route("GET", "/utilities/transformers/codes")
        params = {"search": search, "archived": archived, "hidden": hidden, "playtesting": playtesting}
        return self._request(r, params=params, response_model=OverwatchCode)

    def get_autocomplete_users(
        self,
        search: str,
        *,
        limit: int = 5,
        fake_users_only: bool = False,
    ) -> Response[list[tuple[int, str]]]:
        """Search for users matching the input string.

        Args:
            search (str): The search query.
            limit (int, optional): Max number of results. Defaults to 5.
            fake_users_only (bool): Whether to search only for fake users.

        Returns:
            Response[list[tuple[int, str]]]: Matching users (ID, name).
        """
        r = Route("GET", "/utilities/autocomplete/users")
        params = {"search": search, "limit": limit, "fake_users_only": fake_users_only}
        return self._request(r, params=params, response_model=list[tuple[int, str]] | None)

    async def get_notification_flags(self, user_id: int, *, to_bitmask: bool = True) -> Notification:
        """Retrieve notification settings for a user.

        Args:
            user_id (int): The ID of the user.
            to_bitmask (bool, optional): Whether to return as a bitmask. Defaults to True.

        Returns:
            Notification: The parsed notification flags.
        """
        r = Route("GET", "/users/{user_id}/notifications", user_id=user_id)
        data = await self._request(r, params={"to_bitmask": str(to_bitmask)})
        decoded = msgspec.json.decode(data)
        if to_bitmask:
            bitmask = decoded.get("bitmask")
            return Notification(bitmask or 0)
        notifications = decoded.get("notifications")
        flags = sum((Notification[name] for name in notifications), Notification(0))
        return flags

    def update_notification(self, user_id: int, notification_type: NOTIFICATION_TYPES, data: bool) -> Response[None]:
        """Update a user's notification preference.

        Args:
            user_id: ID of the user whose notification preference is being updated.
            notification_type: The type of notification to update.
            data: Whether the notification should be enabled (True) or disabled (False).

        Returns:
            Response[None]: API response object.
        """
        r = Route(
            "PATCH",
            "/users/{user_id}/notifications/{notification_type}",
            user_id=user_id,
            notification_type=notification_type,
        )
        return self._request(r, data=data)  # pyright: ignore[reportArgumentType]

    def check_user_is_creator(self, user_id: int) -> Response[bool]:
        """Check if user is a creator.

        Args:
            user_id (int): The id of the user to check.

        Returns:
            Response[bool]: True if user is a creator.
        """
        r = Route("GET", "/users/{user_id}/creator", user_id=user_id)
        return self._request(r, response_model=bool)

    def check_user_exists(self, user_id: int) -> Response[bool]:
        """Check whether a user with the given ID exists.

        Args:
            user_id (int): The user ID to check.

        Returns:
            Response[bool]: True if the user exists, otherwise False.
        """
        r = Route("GET", "/users/{user_id}/exists", user_id=user_id)
        return self._request(r, response_model=bool)

    def update_user_names(self, user_id: int, data: UserUpdateDTO) -> Response[None]:
        """Update a user's nickname or global_name.

        Args:
            user_id (int): The user ID to check.
            data (UserUpdateDTO): Data to update.
        """
        r = Route("PATCH", "/users/{user_id}", user_id=user_id)
        return self._request(r, data=data)

    def get_users(self) -> Response[list[UserReadDTO] | None]:
        """Retrieve all users.

        Returns:
            Response[list[User] | None]: List of users, or None if no data.
        """
        r = Route("GET", "/users")
        return self._request(r, response_model=list[UserReadDTO] | None)

    def get_user(self, user_id: int) -> Response[UserReadDTO | None]:
        """Fetch a single user by ID.

        Args:
            user_id (int): The user ID to fetch.

        Returns:
            Response[User | None]: The user, if found.
        """
        r = Route("GET", "/users/{user_id}", user_id=user_id)
        return self._request(r, response_model=UserReadDTO | None)

    def create_user(self, data: UserCreateDTO) -> Response[UserReadDTO]:
        """Create a new user.

        Args:
            data: User creation payload.

        Returns:
            Response[UserReadDTO]: The created user's data.
        """
        r = Route("POST", "/users")
        return self._request(r, response_model=UserReadDTO, data=data)

    def get_user_rank_data(self, user_id: int) -> Response[list[RankDetailReadDTO]]:
        """Fetch detailed rank data for a user.

        Args:
            user_id: ID of the user to retrieve rank data for.

        Returns:
            Response[list[RankDetailReadDTO]]: List of rank detail records.
        """
        r = Route("GET", "/users/{user_id}/rank", user_id=user_id)
        return self._request(r, response_model=list[RankDetailReadDTO])

    def get_affected_users(self, code: OverwatchCode) -> Response[list[int]]:
        """Fetch user IDs affected by a specific map.

        Args:
            code: Overwatch map code.

        Returns:
            Response[list[int]]: List of user IDs affected by the map.
        """
        r = Route("GET", "/maps/{code}/affected", code=code)
        return self._request(r, response_model=list[int])

    def _guess_content_type(self, filename: str | None) -> str:
        ctype, _ = mimetypes.guess_type(filename or "")
        return ctype or "application/octet-stream"

    def submit_completion(
        self,
        data: CompletionCreateDTO,
    ) -> Response[SubmitCompletionReturnDTO]:
        """Submit a new completion with an attached file (multipart/form-data).

        Args:
            data: Completion metadata to send as JSON in the multipart "data" part.
            file: The file payload. Accepts raw bytes, a Path, or a binary file-like object.
            filename: Optional filename to send; falls back to Path name or stream name.
            content_type: Optional MIME type; inferred from filename when not provided.

        Returns:
            Response[int]: The created submission ID.
        """
        r = Route("POST", "/completions")
        return self._request(r, response_model=SubmitCompletionReturnDTO, data=data)

    def get_completion_submission(self, record_id: int) -> Response[CompletionSubmissionModel]:
        """Fetch a completion submission by its record ID.

        Args:
            record_id (int): The ID of the completion submission.

        Returns:
            Response[CompletionSubmissionModel]: The submission data.
        """
        r = Route("GET", "/completions/{record_id}/submission", record_id=record_id)
        return self._request(r, response_model=CompletionSubmissionModel)

    def get_pending_verifications(self) -> Response[list[PendingVerification]]:
        """Retrieve all pending completion verifications.

        Returns:
            Response[list[PendingVerification]]: List of pending verifications.
        """
        r = Route("GET", "/completions/pending")
        return self._request(r, response_model=list[PendingVerification])

    def edit_completion(self, record_id: int, data: CompletionPatchDTO) -> Response[None]:
        """Apply updates to a completion record.

        Args:
            record_id (int): The ID of the completion to update.
            data (CompletionPatchDTO): The patch data.
        """
        r = Route("PATCH", "/completions/{record_id}", record_id=record_id)
        return self._request(r, data=data)

    def verify_completion(self, record_id: int, data: CompletionVerificationPutDTO) -> Response[JobStatus]:
        """Submit a verification decision for a completion.

        Args:
            record_id (int): The ID of the completion.
            data (CompletionVerificationPutDTO): The verification decision.
        """
        r = Route("PUT", "/completions/{record_id}/verification", record_id=record_id)
        return self._request(r, data=data, response_model=JobStatus)

    def get_completions(self, code: OverwatchCode) -> Response[list[CompletionLeaderboardFormattable]]:
        """Fetch the completions leaderboard for a specific map code.

        Args:
            code (OverwatchCode): The map code to check.

        Returns:
            Response[list[CompletionLeaderboardFormattable]]: The completions leaderboard data.
        """
        r = Route("GET", "/completions/{code}", code=code)
        return self._request(r, response_model=list[CompletionLeaderboardFormattable])

    def get_completions_for_user(
        self, user_id: int, difficulty: DifficultyAll | None
    ) -> Response[list[CompletionUserFormattable]]:
        """Fetch the completions for a specific user id.

        Args:
            user_id (int): The user_id to filter by.
            difficulty (DifficultyAll): The difficulty to filter by.

        Returns:
            Response[list[CompletionUserFormattable]]: The completions data for a specific user.
        """
        r = Route("GET", "/completions")
        return self._request(
            r,
            response_model=list[CompletionUserFormattable],
            params={"user_id": user_id, "difficulty": difficulty},
        )

    def get_world_records_for_user(self, user_id: int) -> Response[list[CompletionUserFormattable]]:
        """Fetch the world record completions for a specific user id.

        Args:
            user_id (int): The user_id to filter by.

        Returns:
            Response[list[CompletionUserFormattable]]: The completions data for a specific user.
        """
        r = Route("GET", "/completions/world-records")
        return self._request(r, response_model=list[CompletionUserFormattable], params={"user_id": user_id})

    def create_newsfeed(self, event: NewsfeedEvent) -> Response[CreatePublishNewsfeedReturnDTO]:
        """Create a newsfeed event and return its ID.

        Args:
            event (NewsfeedEvent): Event data.

        Returns:
            (int): Newsfeed event ID.
        """
        r = Route("POST", "/newsfeed")
        return self._request(r, response_model=CreatePublishNewsfeedReturnDTO, data=event)

    def get_suspicious_flags(self, user_id: int) -> Response[list[SuspiciousCompletionModel]]:
        """Fetch suspicious completion flags for a user.

        Args:
            user_id: ID of the user whose suspicious flags should be retrieved.

        Returns:
            Response[list[SuspiciousCompletionModel]]: List of suspicious flag records
            associated with the user.
        """
        r = Route("GET", "/completions/suspicious")
        return self._request(r, response_model=list[SuspiciousCompletionModel], params={"user_id": user_id})

    def set_suspicious_flags(self, data: SuspiciousCompletionWriteDTO) -> Response[SuspiciousCompletionWriteDTO]:
        """Create a suspicious flag for a completion.

        Args:
            data: Payload describing the suspicious flag to set.

        Returns:
            Response[SuspiciousCompletionWriteDTO]: The created suspicious flag record.
        """
        r = Route("POST", "/completions/suspicious")
        return self._request(r, data=data)

    def upvote_submission(self, data: UpvoteCreateDTO) -> Response[UpvoteSubmissionReturnDTO]:
        """Upvote a completion submission.

        Args:
            data: Payload containing the upvote request details.

        Returns:
            Response[UpvoteSubmissionReturnDTO]: The new upvote ID if created, or None if unsuccessful.
        """
        r = Route("POST", "/completions/upvoting")
        return self._request(r, data=data, response_model=UpvoteSubmissionReturnDTO)

    def get_newsfeed(
        self,
        *,
        page_size: Literal[10, 20, 25, 50] = 10,
        page_number: int = 1,
        type_: NewsfeedEventType | None = None,
    ) -> Response[list[NewsfeedEvent]]:
        """List newsfeed events using page size & page number."""
        r = Route("GET", "/newsfeed")
        params = {"page_size": page_size, "page_number": page_number, "type": type_}
        return self._request(r, response_model=list[NewsfeedEvent], params=params)

    def get_newsfeed_event(self, newsfeed_id: int) -> Response[NewsfeedEvent]:
        """Fetch a single newsfeed event by ID."""
        r = Route("GET", "/newsfeed/{newsfeed_id}", newsfeed_id=newsfeed_id)
        return self._request(r, response_model=NewsfeedEvent)

    def upload_image(
        self,
        img_bytes: bytes,
        *,
        filename: str = "screenshot.png",
        content_type: str = "image/png",
    ) -> Response[str]:
        """Upload image to CDN via Lust.

        Returns:
            str: URL of CDN image.
        """
        r = Route("POST", "/utilities/image")
        return self._request_multipart(
            r,
            response_model=str,
            file=img_bytes,
            filename=filename,
            file_field="file",
            content_type=content_type,
        )

    def grant_key_to_user(self, user_id: int, key_type: LootboxKeyType) -> Response[None]:
        """Grant a specific key type to a user.

        Args:
            user_id: ID of the user to receive the key.
            key_type: Type of lootbox key to grant.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/lootbox/users/{user_id}/keys/{key_type}", user_id=user_id, key_type=key_type)
        return self._request(r)

    def grant_active_key_to_user(self, user_id: int) -> Response[None]:
        """Grant an active lootbox key to a user.

        Args:
            user_id: ID of the user to receive the active key.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/lootbox/users/{user_id}/keys", user_id=user_id)
        return self._request(r)

    def set_active_key(self, key_type: LootboxKeyType) -> Response[None]:
        """Set the globally active lootbox key type.

        Args:
            key_type: The key type to activate.

        Returns:
            Response[None]: API response object.
        """
        r = Route("PATCH", "/lootbox/keys/{key_type}", key_type=key_type)
        return self._request(r)

    def grant_user_xp(self, user_id: int, data: XpGrant) -> Response[XpGrantResult]:
        """Grant XP to a user.

        Args:
            user_id: ID of the user to grant XP to.
            data: Payload describing the XP grant.

        Returns:
            Response[XpGrantResult]: Resulting XP grant details.
        """
        r = Route("POST", "/lootbox/users/{user_id}/xp", user_id=user_id)
        return self._request(r, response_model=XpGrantResult, data=data)

    def get_xp_tier_change(self, old_xp: int, new_xp: int) -> Response[TierChange]:
        """Get XP tier changes between old and new XP values.

        Args:
            old_xp: The user's previous XP value.
            new_xp: The user's updated XP value.

        Returns:
            Response[TierChange]: Tier change information.
        """
        r = Route("GET", "/lootbox/xp/tier")
        return self._request(r, response_model=TierChange, params={"old_xp": old_xp, "new_xp": new_xp})

    def get_map_mastery_data(
        self, user_id: int, map_name: OverwatchMap | None = None
    ) -> Response[list[MapMasteryData]]:
        """Fetch mastery data for a user, optionally scoped to a map.

        Args:
            user_id: Target user ID.
            map_name: Optional map name to filter mastery data.

        Returns:
            Response[list[MapMasteryData]]: List of mastery records.
        """
        r = Route("GET", "/maps/mastery")
        return self._request(r, response_model=list[MapMasteryData], params={"user_id": user_id, "map_name": map_name})

    def update_mastery(self, data: MapMasteryCreateDTO) -> Response[MapMasteryCreateReturnDTO | None]:
        """Update mastery progress for a user on a map.

        Args:
            data: Map mastery creation/update payload.

        Returns:
            Response[MapMasteryCreateReturnDTO | None]: Updated mastery data, or None if not applicable.
        """
        r = Route("POST", "/maps/mastery")
        return self._request(r, response_model=MapMasteryCreateReturnDTO | None, data=data)

    def check_permission_for_change_request(self, thread_id: int, user_id: int, code: OverwatchCode) -> Response[bool]:
        """Check whether a user has permission to create a change request.

        Args:
            thread_id: Target thread ID.
            user_id: ID of the requesting user.
            code: Map code involved in the request.

        Returns:
            Response[bool]: True if the user has permission, False otherwise.
        """
        r = Route("GET", "/change-requests/permission")
        return self._request(r, response_model=bool, params={"thread_id": thread_id, "user_id": user_id, "code": code})

    def create_change_request(self, data: ChangeRequestCreateDTO) -> Response[None]:
        """Create a new change request.

        Args:
            data: Change request creation payload.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/change-requests")
        return self._request(r, data=data)

    def resolve_change_request(self, thread_id: int) -> Response[None]:
        """Resolve a change request thread.

        Args:
            thread_id: The thread ID to resolve.

        Returns:
            Response[None]: API response object.
        """
        r = Route("PATCH", "/change-requests/{thread_id}/resolve", thread_id=thread_id)
        return self._request(r)

    def get_change_requests(self, code: OverwatchCode) -> Response[list[FormattableChangeRequest]]:
        """Fetch all change requests for a map.

        Args:
            code: Overwatch map code to fetch requests for.

        Returns:
            Response[list[FormattableChangeRequest]]: List of change requests.
        """
        r = Route("GET", "/change-requests")
        return self._request(r, response_model=list[FormattableChangeRequest], params={"code": code})

    def get_stale_change_requests(self) -> Response[list[FormattableStaleChangeRequest]]:
        """Fetch all stale change requests.

        Returns:
            Response[list[FormattableStaleChangeRequest]]: List of stale requests.
        """
        r = Route("GET", "/change-requests/stale")
        return self._request(r, response_model=list[FormattableStaleChangeRequest])

    def update_alerted_change_request(self, thread_id: int) -> Response[None]:
        """Mark a change request thread as alerted.

        Args:
            thread_id: The change request thread ID.

        Returns:
            Response[None]: API response object.
        """
        r = Route("PATCH", "/change-requests/{thread_id}/alerted", thread_id=thread_id)
        return self._request(r)

    def get_overwatch_usernames(self, user_id: int) -> Response[OverwatchUsernamesReadDTO]:
        """Fetch Overwatch usernames associated with a user.

        Args:
            user_id: ID of the target user.

        Returns:
            Response[OverwatchUsernamesReadDTO]: Usernames DTO for the user.
        """
        r = Route("GET", "/users/{user_id}/overwatch", user_id=user_id)
        return self._request(r, response_model=OverwatchUsernamesReadDTO)

    def update_overwatch_usernames(self, user_id: int, data: OverwatchUsernamesUpdate) -> Response[None]:
        """Update Overwatch usernames for a user.

        Args:
            user_id: ID of the target user.
            data: Update payload for Overwatch usernames.

        Returns:
            Response[None]: API response object.
        """
        r = Route("PUT", "/users/{user_id}/overwatch", user_id=user_id)
        return self._request(r, data=data)

    def convert_map_to_legacy(self, code: OverwatchCode) -> Response[None]:
        """Convert a map to legacy status.

        Args:
            code: Overwatch map code to convert.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/maps/{code}/legacy", code=code)
        return self._request(r)

    def override_quality_votes(self, code: OverwatchCode, data: QualityValueDTO) -> Response[None]:
        """Override quality votes for a map.

        Args:
            code: Overwatch map code.
            data: Quality value payload.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/maps/{code}/quality", code=code)
        return self._request(r, data=data)

    def set_quality_vote(self, code: OverwatchCode, data: QualityUpdateDTO) -> Response[None]:
        """Override quality votes for a map.

        Args:
            code: Overwatch map code.
            data: Quality value payload.

        Returns:
            Response[None]: API response object.
        """
        r = Route("POST", "/completions/{code}/quality", code=code)
        return self._request(r, data=data)

    def create_fake_member(self, name: str) -> Response[int]:
        """Create a fake user with the given name.

        Args:
            name: Display name for the fake user.

        Returns:
            Response[int]: The new fake user's ID.
        """
        r = Route("POST", "/users/fake")
        return self._request(r, params={"name": name})

    def link_fake_member_id_to_real_user_id(self, fake_user_id: int, real_user_id: int) -> Response[None]:
        """Link a fake user ID to a real user ID.

        Reassigns data from the fake user to the real user and removes the fake user.

        Args:
            fake_user_id: ID of the fake user.
            real_user_id: ID of the real user.

        Returns:
            Response[None]: API response object.
        """
        r = Route(
            "POST",
            "/users/fake/{fake_user_id}/link/{real_user_id}",
            fake_user_id=fake_user_id,
            real_user_id=real_user_id,
        )
        return self._request(r)

    def get_xp_multiplier(self) -> Response[float]:
        """Fetch the current XP multiplier.

        Returns:
            Response[float]: Current XP multiplier value.
        """
        r = Route("GET", "/lootbox/xp/multiplier")
        return self._request(r, response_model=float)

    def check_for_previous_world_record_xp(self, code: OverwatchCode, user_id: int) -> Response[bool]:
        """Check whether XP has already been granted for a world record.

        Args:
            code: The map code to check.
            user_id: The user id to check.

        Returns:
            Response[bool]: True if XP was previously granted, False otherwise.
        """
        r = Route("GET", "/completions/{code}/wr-xp-check", code=code)
        return self._request(r, response_model=bool, params={"user_id": user_id})

    def log_analytics(
        self,
        command_name: str,
        user_id: int,
        created_at: datetime.datetime,
        namespace: dict,
    ) -> Response[None]:
        """Log command interactions.

        Args:
            command_name (str): The fully qualified command name used in the interaction.
            user_id (int): The user id of the interaction.
            created_at (datetime): When the interaction occured.
            namespace (dict): A dict of the interaction command namespace (arguments used).
        """
        r = Route("POST", "/utilities/log")
        data = LogCreateDTO(command_name, user_id, created_at, namespace)
        return self._request(r, data=data)

    def get_job(self, job_id: UUID) -> Response[JobStatus]:
        """Get an active job."""
        r = Route("GET", "/internal/jobs/{job_id}", job_id=job_id)
        return self._request(r, response_model=JobStatus)

    def update_job(
        self,
        job_id: UUID,
        status: Literal["processing", "succeeded", "failed", "timeout", "queued"],
        error_code: str | None = None,
        error_msg: str | None = None,
    ) -> Response[None]:
        """Update a job status."""
        r = Route("PATCH", "/internal/jobs/{job_id}", job_id=job_id)
        data = JobUpdate(status, error_code, error_msg)
        return self._request(r, data=data)

    def search_tags(self, data: TagsSearchFilters) -> Response[TagsSearchResponse]:
        """Search / fetch tags via flexible filters (aliases, fuzzy, rank, random, paging)."""
        r = Route("POST", "/tags/search")
        return self._request(r, response_model=TagsSearchResponse, data=data)

    def mutate_tags(self, data: TagsMutateRequest) -> Response[TagsMutateResponse]:
        """Perform tag mutations (create, alias, edit, remove, claim, transfer, purge, increment)."""
        r = Route("POST", "/tags/mutate")
        return self._request(r, response_model=TagsMutateResponse, data=data)

    def autocomplete_tags(self, data: TagsAutocompleteRequest) -> Response[TagsAutocompleteResponse]:
        """Autocomplete tag names (aliased/non-aliased, owned variants)."""
        r = Route("POST", "/tags/autocomplete")
        return self._request(r, response_model=TagsAutocompleteResponse, data=data)


async def setup(bot: core.Genji) -> None:
    """Set up the HTTP client extension."""
    bot.api = APIClient()
