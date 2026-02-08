from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

from app.db.models import MediaKind, Pack
from app.db.repo import Database
from app.services.telegram_sticker_api import TelegramStickerApi


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return slug or "stickerpack"


def _username_lc(username: str | None) -> str | None:
    if not username:
        return None
    raw = username.strip().lstrip("@").lower()
    if not USERNAME_RE.fullmatch(raw):
        return None
    return raw


def generate_short_name(title: str, tg_user_id: int, bot_username: str, salt: str) -> str:
    safe_bot_username = re.sub(r"[^a-zA-Z0-9_]+", "", bot_username).lower() or "bot"
    suffix = f"_by_{safe_bot_username}"
    max_base_len = max(1, 64 - len(suffix))
    base = _slugify(title)
    hash_part = hashlib.sha1(f"{tg_user_id}:{salt}:{title}".encode("utf-8")).hexdigest()[:8]
    candidate = f"{base}_{hash_part}"
    candidate = candidate[:max_base_len].rstrip("_")
    if not candidate:
        candidate = "pack"
    return f"{candidate}{suffix}"


class PackService:
    def __init__(self, db: Database, tg_api: TelegramStickerApi, bot_username: str) -> None:
        self.db = db
        self.tg_api = tg_api
        self.bot_username = bot_username

    async def create_draft_pack(self, tg_user_id: int, title: str, username: str | None = None) -> Pack:
        user_id = await self.db.ensure_user(tg_user_id, _username_lc(username))
        short_name = generate_short_name(title=title, tg_user_id=tg_user_id, bot_username=self.bot_username, salt="draft")
        return await self.db.create_draft_pack(user_id=user_id, title=title, short_name=short_name)

    async def list_packs(self, tg_user_id: int, username: str | None = None) -> list[Pack]:
        user_id = await self.db.ensure_user(tg_user_id, _username_lc(username))
        return await self.db.list_packs_for_user(user_id=user_id)

    async def get_active_pack(self, tg_user_id: int, username: str | None = None) -> Pack | None:
        user_id = await self.db.ensure_user(tg_user_id, _username_lc(username))
        return await self.db.get_active_pack(user_id=user_id)

    async def activate_pack(self, tg_user_id: int, pack_id: int, username: str | None = None) -> bool:
        user_id = await self.db.ensure_user(tg_user_id, _username_lc(username))
        return await self.db.activate_pack(user_id=user_id, pack_id=pack_id)

    async def add_processed_sticker(
        self,
        *,
        tg_user_id: int,
        media_kind: MediaKind,
        sticker_path: Path,
        emoji: str,
        username: str | None = None,
    ) -> Pack:
        user_id = await self.db.ensure_user(tg_user_id, _username_lc(username))
        active = await self.db.get_active_pack(user_id=user_id)
        if not active:
            raise RuntimeError("У вас нет активного стикерпака. Создайте его через /newpack")

        owner_tg_user_id = await self.db.get_pack_owner_tg_user_id(active.id)
        if owner_tg_user_id is None:
            raise RuntimeError("Не удалось определить владельца стикерпака")

        if active.status.value == "draft" or not active.tg_set_name:
            tg_set_name = await self._create_new_set_with_fallback_names(
                owner_tg_user_id=owner_tg_user_id,
                pack=active,
                media_kind=media_kind,
                sticker_path=sticker_path,
                emoji=emoji,
            )
            await self.db.set_pack_ready(pack_id=active.id, tg_set_name=tg_set_name)
            pack = await self.db.get_pack_by_id(active.id, requester_user_id=user_id)
            if not pack:
                raise RuntimeError("Pack not found after create")
            return pack

        await self.tg_api.add_sticker(
            tg_user_id=owner_tg_user_id,
            tg_set_name=active.tg_set_name,
            media_kind=media_kind,
            sticker_path=sticker_path,
            emoji=emoji,
        )
        pack = await self.db.get_pack_by_id(active.id, requester_user_id=user_id)
        if not pack:
            raise RuntimeError("Pack not found after add")
        return pack

    async def _create_new_set_with_fallback_names(
        self,
        *,
        owner_tg_user_id: int,
        pack: Pack,
        media_kind: MediaKind,
        sticker_path: Path,
        emoji: str,
    ) -> str:
        attempts = [pack.short_name] + [
            generate_short_name(
                title=pack.title,
                tg_user_id=owner_tg_user_id,
                bot_username=self.bot_username,
                salt=f"retry-{idx}",
            )
            for idx in range(1, 4)
        ]

        last_error: Exception | None = None
        for candidate in attempts:
            try:
                created_name = await self.tg_api.create_set(
                    tg_user_id=owner_tg_user_id,
                    title=pack.title,
                    short_name=candidate,
                    media_kind=media_kind,
                    sticker_path=sticker_path,
                    emoji=emoji,
                )
                if candidate != pack.short_name:
                    await self.db.update_pack_short_name(pack_id=pack.id, short_name=candidate)
                return created_name
            except TelegramBadRequest as exc:
                text = str(exc).lower()
                last_error = exc
                if "sticker set name is already occupied" in text or "short name" in text:
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Failed to create sticker set")
