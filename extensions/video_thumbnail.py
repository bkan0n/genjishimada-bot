from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from yarl import URL

if TYPE_CHECKING:
    from core.genji import Genji

YOUTUBE_URL_REGEX = re.compile(
    r"^((https?://(?:www\.)?(?:m\.)?youtube\.com))/((?:oembed\?url=https?%3A//(?:www\.)youtube.com/watch\?(?:v%3D)"
    r"(?P<video_id_1>[\w\-]{10,20})&format=json)|(?:attribution_link\?a=.*watch(?:%3Fv%3D|%3Fv%3D)(?P<video_id_2>[\w\-]{10,20}))"
    r"(?:%26feature.*))|(https?:)?(\/\/)?((www\.|m\.)?youtube(-nocookie)?\.com\/((watch)?\?(app=desktop&)?(feature=\w*&)"
    r"?v=|embed\/|v\/|e\/)|youtu\.be\/)(?P<video_id_3>[\w\-]{10,20})",
    re.IGNORECASE,
)


def _trim_url_keep_path(url: str) -> str:
    """Remove query/fragment to normalize path-based ID extraction."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _normalize_image_url(u: str) -> str:
    """Ensure image URL has a scheme (Bilibili sometimes returns //host/path)."""
    if u.startswith("//"):
        return "https:" + u
    return u


def _extract_bilibili_video_id(url: str) -> Optional[Tuple[str, str]]:
    """Extract ('bvid','BV...') or ('aid','123456') from a Bilibili URL.

    Works for:
      https://www.bilibili.com/video/BV1sQtmzcE5x
      https://m.bilibili.com/video/BVxxxxxxx?p=2 (query ignored)
      .../video/av123456
    """
    clean = _trim_url_keep_path(url)
    path = urlsplit(clean).path
    segments = [s for s in path.split("/") if s]

    candidate = None
    if len(segments) >= 2 and segments[0].lower() == "video":  # noqa: PLR2004
        candidate = segments[1]
    if candidate is None:
        candidate = next(
            (s for s in segments if s.startswith("BV") or s.lower().startswith("av")),
            None,
        )
    if not candidate:
        return None

    if candidate.lower().startswith("av"):
        num = candidate[2:]
        return ("aid", num) if num.isdigit() else None

    if candidate.startswith("BV"):
        return ("bvid", candidate)

    return None


class ThumbnailProvider(ABC):
    """Abstract thumbnail provider."""

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Return True if this provider can handle the URL."""
        raise NotImplementedError

    @abstractmethod
    async def get_thumbnail(self, url: str) -> Optional[str]:
        """Return a direct thumbnail URL or None if not resolvable."""
        raise NotImplementedError


class YouTubeProvider(ThumbnailProvider):
    """YouTube: extract the video ID and return img.youtube.com maxres thumbnail."""

    def __init__(self) -> None:
        """Initialize YouTubeProvider."""
        self._regex = YOUTUBE_URL_REGEX

    def matches(self, url: str) -> bool:
        """Match a URL."""
        return bool(self._regex.match(url))

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = YOUTUBE_URL_REGEX.match(url)
        if not match:
            return None
        return match.group("video_id_1") or match.group("video_id_2") or match.group("video_id_3")

    async def get_thumbnail(self, url: str) -> Optional[str]:
        """Get the thumbnail URL."""
        vid = self._extract_video_id(url)
        if not vid:
            return None
        return f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"


class BilibiliProvider(ThumbnailProvider):
    """Bilibili: resolve b23 shortlinks, extract BV/av, call view API and return data.pic."""

    _API_URL = URL("https://api.bilibili.com/x/web-interface/view")

    def __init__(
        self,
        session: aiohttp.ClientSession,
        timeout: float = 6.0,
    ) -> None:
        """Initialize the BilibiliProvider."""
        self._session = session
        self._timeout = timeout

    def matches(self, url: str) -> bool:
        """Match the URL."""
        host = urlsplit(url).netloc.lower()
        return "bilibili.com" in host or "b23.tv" in host

    async def _resolve_short_url(self, url: str) -> str:
        """Follow redirects for b23.tv (HEAD then GET fallback)."""
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with self._session.head(url, allow_redirects=True, timeout=timeout) as r:
                return str(r.url) if r.url else url
        except Exception:
            pass
        try:
            async with self._session.get(url, allow_redirects=True, timeout=timeout) as r:
                return str(r.url) if r.url else url
        except Exception:
            return url

    async def get_thumbnail(self, url: str) -> Optional[str]:
        """Get the thumbnail URL."""
        parts = urlsplit(url)
        if "b23.tv" in parts.netloc.lower():
            url = await self._resolve_short_url(url)

        idinfo = _extract_bilibili_video_id(url)
        if not idinfo:
            return None

        id_type, id_value = idinfo
        params = {id_type: id_value}
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        headers = {
            "User-Agent": "Mozilla/5.0 (ThumbnailFetcher/1.0)",
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json,text/plain,*/*",
        }

        try:
            async with self._session.get(self._API_URL, params=params, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:  # noqa: PLR2004
                    return None
                payload = await resp.json(content_type=None)
        except Exception:
            return None

        if not isinstance(payload, dict) or payload.get("code") != 0:
            return None

        data = payload.get("data") or {}
        pic = data.get("pic")
        if not isinstance(pic, str) or not pic:
            return None
        return _normalize_image_url(pic)


class VideoThumbnailService:
    _providers: Sequence[ThumbnailProvider]

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        providers: Optional[Sequence[ThumbnailProvider]] = [],
        fallback: Optional[str] = None,
    ) -> None:
        """Initialize the VideoThumbnailService."""
        self._session = session
        self._providers = [
            YouTubeProvider(),
            BilibiliProvider(session=self._session),
        ]
        self._fallback = fallback

    async def get_thumbnail(self, url: str) -> str:
        """Return a direct thumbnail URL if resolved; otherwise fallback or original URL.

        Args:
            url: The input video/page URL.
        """
        for p in self._providers:
            if p.matches(url):
                thumb = await p.get_thumbnail(url)
                if thumb:
                    return thumb
        return self._fallback if self._fallback is not None else url


async def setup(bot: Genji) -> None:
    """Set up VideoThumbnailService."""
    bot.thumbnail_service = VideoThumbnailService(bot.session, fallback="https://cdn.genji.pk/assets/no-thumbnail.jpg")
