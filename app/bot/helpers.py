from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db.models import MediaKind
from app.services.media_service import MediaService


@dataclass(slots=True)
class IncomingMedia:
    file_id: str
    file_unique_id: str
    mime: str | None
    filename: str | None
    media_kind: MediaKind


async def ensure_allowed_event(event: Message | CallbackQuery, settings: Settings) -> bool:
    user = event.from_user
    if user is None:
        text = "Не удалось определить пользователя."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return False

    if isinstance(event, Message):
        if event.chat and event.chat.type != "private":
            await event.answer("MVP-версия работает только в личных сообщениях.")
            return False
    else:
        if event.message and event.message.chat.type != "private":
            await event.answer("MVP-версия работает только в личных сообщениях.", show_alert=True)
            return False
    return True


def extract_media_from_message(message: Message) -> IncomingMedia | None:
    if message.photo:
        largest = message.photo[-1]
        return IncomingMedia(
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            mime="image/jpeg",
            filename="photo.jpg",
            media_kind=MediaKind.IMAGE,
        )

    if message.video:
        video = message.video
        return IncomingMedia(
            file_id=video.file_id,
            file_unique_id=video.file_unique_id,
            mime=video.mime_type,
            filename=video.file_name,
            media_kind=MediaKind.VIDEO,
        )

    if message.animation:
        animation = message.animation
        return IncomingMedia(
            file_id=animation.file_id,
            file_unique_id=animation.file_unique_id,
            mime=animation.mime_type or "video/mp4",
            filename=animation.file_name or "animation.gif",
            media_kind=MediaKind.VIDEO,
        )

    if message.document:
        doc = message.document
        kind = MediaService.infer_media_kind(doc.mime_type, doc.file_name)
        if not kind:
            return None
        return IncomingMedia(
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            mime=doc.mime_type,
            filename=doc.file_name,
            media_kind=kind,
        )

    return None


async def download_file(bot: Bot, file_id: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=str(destination))
