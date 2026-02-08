from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PackStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class JobStatus(StrEnum):
    PENDING = "pending"
    CROP_CHOSEN = "crop_chosen"
    EMOJI_CHOSEN = "emoji_chosen"
    DONE = "done"
    ERROR = "error"


class CropMode(StrEnum):
    SQUARE = "square"
    FIT = "fit"


@dataclass(slots=True)
class User:
    id: int
    tg_user_id: int
    created_at: datetime


@dataclass(slots=True)
class Pack:
    id: int
    user_id: int
    title: str
    short_name: str
    tg_set_name: str | None
    status: PackStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class MediaJob:
    id: int
    user_id: int
    telegram_file_id: str
    telegram_file_unique_id: str
    media_kind: MediaKind
    mime: str | None
    original_name: str | None
    temp_path: str
    crop_mode: CropMode | None
    processed_path: str | None
    preview_path: str | None
    suggestions_json: str | None
    status: JobStatus
    error_text: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class StickerRecord:
    id: int
    pack_id: int
    media_kind: MediaKind
    emoji: str
    telegram_file_id: str | None
    source_hash: str
    created_at: datetime
