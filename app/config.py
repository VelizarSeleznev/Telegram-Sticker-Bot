from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    bot_token: str
    db_path: Path
    temp_dir: Path
    log_level: str
    max_concurrent_jobs: int
    polling_timeout: int
    gemini_api_key: str
    emoji_vision_model: str
    emoji_vision_timeout_seconds: float
    emoji_vision_max_output_tokens: int
    klipy_api_key: str
    klipy_client_key: str
    klipy_locale: str
    klipy_country: str
    klipy_content_filter: str

    @classmethod
    def from_env(cls) -> "Settings":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")

        db_path = Path(os.getenv("DB_PATH", "/data/bot.db")).expanduser().resolve()
        temp_dir = Path(os.getenv("TEMP_DIR", "/tmp/sticker-bot")).expanduser().resolve()
        log_level = os.getenv("LOG_LEVEL", "INFO").upper().strip() or "INFO"
        max_concurrent_jobs = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
        polling_timeout = int(os.getenv("POLLING_TIMEOUT", "30"))
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required")
        emoji_vision_model = os.getenv("EMOJI_VISION_MODEL", "gemma-4-26b-a4b-it").strip()
        emoji_vision_timeout_seconds = float(os.getenv("EMOJI_VISION_TIMEOUT_SECONDS", "30"))
        emoji_vision_max_output_tokens = int(os.getenv("EMOJI_VISION_MAX_OUTPUT_TOKENS", "192"))
        klipy_api_key = os.getenv("KLIPY_API_KEY", "").strip()
        klipy_client_key = os.getenv("KLIPY_CLIENT_KEY", "otter_sticker_bot").strip() or "otter_sticker_bot"
        klipy_locale = os.getenv("KLIPY_LOCALE", "ru_RU").strip() or "ru_RU"
        klipy_country = os.getenv("KLIPY_COUNTRY", "US").strip() or "US"
        klipy_content_filter = os.getenv("KLIPY_CONTENT_FILTER", "medium").strip() or "medium"

        return cls(
            bot_token=bot_token,
            db_path=db_path,
            temp_dir=temp_dir,
            log_level=log_level,
            max_concurrent_jobs=max_concurrent_jobs,
            polling_timeout=polling_timeout,
            gemini_api_key=gemini_api_key,
            emoji_vision_model=emoji_vision_model,
            emoji_vision_timeout_seconds=emoji_vision_timeout_seconds,
            emoji_vision_max_output_tokens=emoji_vision_max_output_tokens,
            klipy_api_key=klipy_api_key,
            klipy_client_key=klipy_client_key,
            klipy_locale=klipy_locale,
            klipy_country=klipy_country,
            klipy_content_filter=klipy_content_filter,
        )
