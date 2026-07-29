import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.db.models import MediaKind
from app.services.emoji_service import DEFAULT_MODEL, EmojiService


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _write_image(path: Path) -> None:
    Image.new("RGB", (64, 64), "purple").save(path)


def test_emoji_service_uses_gemma_response(monkeypatch, tmp_path):
    image = tmp_path / "preview.png"
    _write_image(image)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return _JsonResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "emojis": ["😭", "😔", "😿"],
                                            "meaning": "грусть",
                                            "ocr": "О ГОРЕ",
                                        },
                                        ensure_ascii=False,
                                    )
                                }
                            ]
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    service = EmojiService(api_key="test-key", timeout_seconds=17)

    result = service.suggest(preview_path=image, media_kind=MediaKind.IMAGE)

    assert result.top3 == ["😭", "😔", "😿"]
    assert result.auto_pick == "😭"
    assert result.confidence == 1.0
    assert f"/models/{DEFAULT_MODEL}:generateContent" in captured["url"]
    assert captured["timeout"] == 17
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    inline_data = captured["payload"]["contents"][0]["parts"][1]["inlineData"]
    assert inline_data["mimeType"] == "image/png"
    assert inline_data["data"]


def test_emoji_service_falls_back_without_key(monkeypatch, tmp_path):
    image = tmp_path / "preview.png"
    _write_image(image)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("network should not be called without GEMINI_API_KEY")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_request)
    service = EmojiService(api_key="")

    result = service.suggest(preview_path=image, media_kind=MediaKind.IMAGE)

    assert result.top3 == ["🖼️", "✨", "🔥"]
    assert result.auto_pick == "🖼️"
    assert result.confidence == 0.0


def test_emoji_service_rejects_non_emoji_model_values(monkeypatch, tmp_path):
    image = tmp_path / "preview.png"
    _write_image(image)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _JsonResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "emojis": ["😭", " ", "sad"],
                                            "meaning": "грусть",
                                            "ocr": "",
                                        },
                                        ensure_ascii=False,
                                    )
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    )
    service = EmojiService(api_key="test-key")

    result = service.suggest(preview_path=image, media_kind=MediaKind.IMAGE)

    assert result.top3 == ["🖼️", "✨", "🔥"]
    assert result.confidence == 0.0


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is required",
)
def test_video_suggestion_sends_three_frame_contact_sheet(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    preview = tmp_path / "preview.png"
    _write_image(preview)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1:size=320x180:rate=12",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )

    captured = {}
    service = EmojiService(api_key="test-key")

    def fake_request(image_path):
        captured["path"] = image_path
        with Image.open(image_path) as image:
            captured["size"] = image.size
        return ["🎬", "😂", "🔥"]

    monkeypatch.setattr(service, "_request_emojis", fake_request)

    result = service.suggest(
        preview_path=preview,
        media_kind=MediaKind.VIDEO,
        video_path=video,
    )

    assert result.top3 == ["🎬", "😂", "🔥"]
    assert captured["path"].name == "emoji-contact-sheet.jpg"
    assert captured["size"] == (1152, 384)
