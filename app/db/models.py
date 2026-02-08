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


class MemberRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"

class StickerActionKind(StrEnum):
    DELETE = "delete"
    IMPORT = "import"


@dataclass(slots=True)
class User:
    id: int
    tg_user_id: int
    username_lc: str | None
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
    role: MemberRole
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
    original_emoji: str | None
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


@dataclass(slots=True)
class PackMember:
    pack_id: int
    user_id: int
    role: MemberRole
    invited_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    tg_user_id: int
    username_lc: str | None


@dataclass(slots=True)
class PackInvitation:
    id: int
    pack_id: int
    inviter_user_id: int
    invited_username_lc: str
    invited_user_id: int | None
    token: str
    status: InvitationStatus
    expires_at: datetime
    accepted_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    pack_title: str | None = None


@dataclass(slots=True)
class StickerAction:
    id: int
    user_id: int
    action_token: str
    kind: StickerActionKind
    sticker_file_id: str
    sticker_file_unique_id: str
    sticker_set_name: str | None
    original_emoji: str | None
    created_at: datetime
    expires_at: datetime
