import pytest

from app.services.klipy_service import KlipyService


def test_build_search_url_uses_klipy_endpoint_and_inline_defaults():
    service = KlipyService(api_key="key")

    url = service.build_search_url("наши мозги думают", offset="next")

    assert url.startswith("https://api.klipy.com/v2/search?")
    assert "q=%D0%BD%D0%B0%D1%88%D0%B8+" in url
    assert "key=key" in url
    assert "client_key=otter_sticker_bot" in url
    assert "locale=ru_RU" in url
    assert "contentfilter=medium" in url
    assert "media_filter=tinymp4%2Cmp4%2Cnanomp4%2Cnanogif%2Ctinygif%2Cgif" in url
    assert "pos=next" in url


def test_to_inline_gif_prefers_tinymp4_and_mp4_thumbnail():
    service = KlipyService(api_key="key")

    result = service._to_inline_gif(
        {
            "id": "123",
            "content_description": "thinking otter",
            "media_formats": {
                "tinymp4": {
                    "url": "https://media.klipy.com/tiny.mp4",
                    "dims": [220, 160],
                    "duration": 2.6,
                },
                "nanomp4": {
                    "url": "https://media.klipy.com/nano.mp4",
                    "dims": [90, 66],
                },
            },
        }
    )

    assert result is not None
    assert result.id == "klipy-123"
    assert result.mpeg4_url == "https://media.klipy.com/tiny.mp4"
    assert result.thumbnail_url == "https://media.klipy.com/nano.mp4"
    assert result.thumbnail_mime_type == "video/mp4"
    assert result.mpeg4_width == 220
    assert result.mpeg4_height == 160
    assert result.mpeg4_duration == 3
    assert result.title is None


def test_to_inline_gif_skips_results_without_media():
    service = KlipyService(api_key="key")

    result = service._to_inline_gif({"id": "123", "media_formats": {}})

    assert result is None


@pytest.mark.asyncio
async def test_search_inline_gifs_skips_empty_queries():
    service = KlipyService(api_key="key")

    result = await service.search_inline_gifs("   ")

    assert result.results == []
    assert result.next_offset == ""


@pytest.mark.asyncio
async def test_search_inline_gifs_deduplicates_klipy_result_ids(monkeypatch):
    service = KlipyService(api_key="key")

    async def fake_fetch_json(url):
        return {
            "next": "next-page",
            "results": [
                {
                    "id": "same",
                    "media_formats": {
                        "tinymp4": {"url": "https://media.klipy.com/1.mp4"},
                        "nanomp4": {"url": "https://media.klipy.com/1-thumb.mp4"},
                    },
                },
                {
                    "id": "same",
                    "media_formats": {
                        "tinymp4": {"url": "https://media.klipy.com/2.mp4"},
                        "nanomp4": {"url": "https://media.klipy.com/2-thumb.mp4"},
                    },
                },
            ],
        }

    monkeypatch.setattr("app.services.klipy_service._fetch_json", fake_fetch_json)

    result = await service.search_inline_gifs("cat")

    assert result.next_offset == "next-page"
    assert len(result.results) == 1
    assert result.results[0].id == "klipy-same"
