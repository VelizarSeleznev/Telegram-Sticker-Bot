from __future__ import annotations

import base64
import json
import logging
import mimetypes
import shutil
import subprocess
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.db.models import MediaKind

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemma-4-26b-a4b-it"

PROMPT = """\
You choose emoji tags for a Telegram sticker. Analyze the whole image as a meme:
read visible text, infer the joke, emotion, and intended reaction. Return exactly
three distinct standard Unicode emoji ordered from best to weakest. Prefer the
meme's meaning/reaction over merely naming an object in the picture.

Return JSON only:
{"emojis":["…","…","…"],"meaning":"short Russian explanation","ocr":"visible text or empty"}
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "emojis": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "meaning": {"type": "string"},
        "ocr": {"type": "string"},
    },
    "required": ["emojis", "meaning", "ocr"],
    "additionalProperties": False,
}


class EmojiProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class EmojiSuggestion:
    top3: list[str]
    auto_pick: str
    confidence: float


class EmojiService:
    """Suggest emoji with Gemma 4 through Google's Generative Language API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 192,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.max_output_tokens = max(64, max_output_tokens)
        if not self.api_key:
            logger.warning("GEMINI_API_KEY is missing; emoji suggestions will use the local reserve")

    def suggest(
        self,
        preview_path: Path,
        media_kind: MediaKind,
        video_path: Path | None = None,
    ) -> EmojiSuggestion:
        fallback = self._fallback(media_kind)
        if not self.api_key:
            return fallback

        request_image = preview_path
        try:
            if media_kind == MediaKind.VIDEO and video_path is not None:
                request_image = self._build_video_contact_sheet(
                    video_path=video_path,
                    output_dir=preview_path.parent,
                )
            top3 = self._request_emojis(request_image)
            return EmojiSuggestion(top3=top3, auto_pick=top3[0], confidence=1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Gemma emoji suggestion failed model=%s error=%s",
                self.model,
                type(exc).__name__,
            )
            return fallback

    @staticmethod
    def _fallback(media_kind: MediaKind) -> EmojiSuggestion:
        first = "🎬" if media_kind == MediaKind.VIDEO else "🖼️"
        defaults = [first, "✨", "🔥"]
        return EmojiSuggestion(top3=defaults, auto_pick=first, confidence=0.0)

    def _request_emojis(self, image_path: Path) -> list[str]:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT},
                        {
                            "inlineData": {
                                "mimeType": mime,
                                "data": image_data,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": RESPONSE_SCHEMA,
            },
        }
        safe_model = urllib.parse.quote(self.model, safe="-._")
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{safe_model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "otter-sticker-bot-emoji/1",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise EmojiProviderError(f"Gemma HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise EmojiProviderError("Gemma network error") from exc

        try:
            parts = body["candidates"][0]["content"]["parts"]
            text = "".join(
                str(part.get("text", ""))
                for part in parts
                if not part.get("thought")
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise EmojiProviderError("Gemma response has no text") from exc
        return self._parse_emojis(text)

    @classmethod
    def _parse_emojis(cls, text: str) -> list[str]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise EmojiProviderError("Gemma response is not JSON") from exc

        raw = payload.get("emojis")
        if not isinstance(raw, list):
            raise EmojiProviderError("Gemma response has no emoji list")
        emojis = [str(value).strip() for value in raw]
        if (
            len(emojis) != 3
            or len(set(emojis)) != 3
            or any(not cls._looks_like_emoji(value) for value in emojis)
        ):
            raise EmojiProviderError("Gemma response has invalid emoji")
        return emojis

    @staticmethod
    def _looks_like_emoji(value: str) -> bool:
        if not value or len(value) > 16 or any(ch.isspace() for ch in value):
            return False
        if any(ord(ch) < 128 for ch in value) and "\u20e3" not in value:
            return False
        return any(
            unicodedata.category(ch) == "So"
            or 0x1F1E6 <= ord(ch) <= 0x1F1FF
            or 0x1F300 <= ord(ch) <= 0x1FAFF
            or 0x2600 <= ord(ch) <= 0x27BF
            or ch == "\u20e3"
            for ch in value
        )

    def _build_video_contact_sheet(self, *, video_path: Path, output_dir: Path) -> Path:
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise EmojiProviderError("ffmpeg is unavailable for video analysis")

        duration = self._video_duration(video_path)
        if duration <= 0:
            raise EmojiProviderError("video duration is unavailable")

        frame_dir = output_dir / "emoji_vision_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        timestamps = [duration * 0.12, duration * 0.5, duration * 0.88]
        frame_paths: list[Path] = []
        for index, timestamp in enumerate(timestamps):
            frame_path = frame_dir / f"frame-{index}.png"
            command = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_path),
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode == 0 and frame_path.exists():
                frame_paths.append(frame_path)

        if not frame_paths:
            raise EmojiProviderError("video frames could not be extracted")

        tile_size = 384
        sheet = Image.new("RGB", (tile_size * len(frame_paths), tile_size), "white")
        for index, frame_path in enumerate(frame_paths):
            with Image.open(frame_path) as frame:
                tile = ImageOps.contain(
                    frame.convert("RGB"),
                    (tile_size, tile_size),
                    Image.Resampling.LANCZOS,
                )
            x = index * tile_size + (tile_size - tile.width) // 2
            y = (tile_size - tile.height) // 2
            sheet.paste(tile, (x, y))

        output_path = output_dir / "emoji-contact-sheet.jpg"
        sheet.save(output_path, "JPEG", quality=86, optimize=True)
        return output_path

    @staticmethod
    def _video_duration(video_path: Path) -> float:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return 0.0
        try:
            payload = json.loads(result.stdout)
            return float((payload.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0.0
