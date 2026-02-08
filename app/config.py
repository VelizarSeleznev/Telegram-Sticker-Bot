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
    emoji_catalog_path: Path

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
        emoji_catalog_path = Path(__file__).resolve().parent / "assets" / "emoji_catalog.json"

        return cls(
            bot_token=bot_token,
            db_path=db_path,
            temp_dir=temp_dir,
            log_level=log_level,
            max_concurrent_jobs=max_concurrent_jobs,
            polling_timeout=polling_timeout,
            emoji_catalog_path=emoji_catalog_path,
        )
