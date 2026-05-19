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
    assert "media_filter=tinygif%2Cnanogif%2Cgif" in url
    assert "pos=next" in url


def test_to_inline_gif_prefers_tinygif_and_nanogif():
    service = KlipyService(api_key="key")

    result = service._to_inline_gif(
        {
            "id": "123",
            "content_description": "thinking otter",
            "media_formats": {
                "tinygif": {
                    "url": "https://media.klipy.com/tiny.gif",
                    "dims": [220, 160],
                    "duration": 2.6,
                },
                "nanogif": {
                    "url": "https://media.klipy.com/nano.gif",
                    "dims": [90, 66],
                },
            },
        }
    )

    assert result is not None
    assert result.id == "klipy-123"
    assert result.gif_url == "https://media.klipy.com/tiny.gif"
    assert result.thumbnail_url == "https://media.klipy.com/nano.gif"
    assert result.gif_width == 220
    assert result.gif_height == 160
    assert result.gif_duration == 3
    assert result.title == "thinking otter"


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
