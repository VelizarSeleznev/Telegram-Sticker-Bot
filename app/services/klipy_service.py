from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from aiogram.types import InlineQueryResultGif


KLIPY_SEARCH_URL = "https://api.klipy.com/v2/search"
DEFAULT_LIMIT = 20


@dataclass(slots=True)
class KlipySearchResult:
    results: list[InlineQueryResultGif]
    next_offset: str


@dataclass(slots=True)
class KlipyService:
    api_key: str
    client_key: str = "otter_sticker_bot"
    locale: str = "ru_RU"
    country: str = "US"
    content_filter: str = "medium"
    limit: int = DEFAULT_LIMIT

    async def search_inline_gifs(self, query: str, *, offset: str = "") -> KlipySearchResult:
        trimmed = query.strip()
        if not trimmed or not self.api_key:
            return KlipySearchResult(results=[], next_offset="")

        payload = await _fetch_json(self.build_search_url(trimmed, offset=offset))
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raw_results = []

        results = []
        for raw_result in raw_results:
            result = self._to_inline_gif(raw_result)
            if result is not None:
                results.append(result)

        next_offset = payload.get("next")
        return KlipySearchResult(
            results=results,
            next_offset=next_offset if isinstance(next_offset, str) else "",
        )

    def build_search_url(self, query: str, *, offset: str = "") -> str:
        params = {
            "q": query,
            "key": self.api_key,
            "client_key": self.client_key,
            "locale": self.locale,
            "country": self.country,
            "contentfilter": self.content_filter,
            "media_filter": "tinygif,nanogif,gif",
            "limit": str(self.limit),
        }
        if offset:
            params["pos"] = offset

        return f"{KLIPY_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    def _to_inline_gif(self, raw_result: object) -> InlineQueryResultGif | None:
        if not isinstance(raw_result, dict):
            return None

        result_id = raw_result.get("id")
        media_formats = raw_result.get("media_formats")
        if not isinstance(result_id, str) or not isinstance(media_formats, dict):
            return None

        gif = _media_format(media_formats, "tinygif") or _media_format(media_formats, "gif")
        thumbnail = (
            _media_format(media_formats, "nanogif")
            or _media_format(media_formats, "tinygif")
            or _media_format(media_formats, "gif")
        )
        if gif is None or thumbnail is None:
            return None

        title = raw_result.get("content_description")
        if not isinstance(title, str) or not title:
            tags = raw_result.get("tags")
            title = ", ".join(tag for tag in tags[:3] if isinstance(tag, str)) if isinstance(tags, list) else ""
        if not title:
            title = "Klipy GIF"

        width, height = _dims(gif.get("dims"))
        duration = gif.get("duration")

        return InlineQueryResultGif(
            id=_telegram_inline_result_id(result_id),
            gif_url=gif["url"],
            thumbnail_url=thumbnail["url"],
            gif_width=width,
            gif_height=height,
            gif_duration=round(duration) if isinstance(duration, int | float) else None,
            title=title,
        )


async def _fetch_json(url: str) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(_fetch_json_sync, url)


def _fetch_json_sync(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "otter-sticker-bot/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Klipy search failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Klipy search failed: {exc.reason}") from exc

    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _media_format(media_formats: dict[object, object], name: str) -> dict[str, Any] | None:
    media = media_formats.get(name)
    if not isinstance(media, dict):
        return None

    url = media.get("url")
    if not isinstance(url, str) or not url:
        return None

    return media


def _dims(raw_dims: object) -> tuple[int | None, int | None]:
    if not isinstance(raw_dims, list | tuple) or len(raw_dims) != 2:
        return None, None

    width, height = raw_dims
    if not isinstance(width, int) or not isinstance(height, int):
        return None, None

    return width, height


def _telegram_inline_result_id(klipy_id: str) -> str:
    result_id = f"klipy-{klipy_id}"
    if len(result_id.encode("utf-8")) <= 64:
        return result_id

    return f"klipy-{sha256(klipy_id.encode('utf-8')).hexdigest()[:24]}"
