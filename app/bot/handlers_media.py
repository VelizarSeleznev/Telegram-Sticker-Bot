from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from aiogram import F, Bot, Router
from aiogram.enums import StickerType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import download_file, ensure_allowed_event, extract_media_from_message
from app.bot.keyboards import crop_keyboard, emoji_keyboard
from app.config import Settings
from app.db.models import CropMode, JobStatus, MediaKind
from app.db.repo import Database
from app.services.emoji_service import EmojiService
from app.services.media_service import MediaService
from app.services.pack_service import PackService

router = Router(name="media")


@router.message(F.photo | F.video | F.document | F.animation | F.sticker)
async def handle_media_upload(
    message: Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    pack_service: PackService,
    media_service: MediaService,
    emoji_service: EmojiService,
    media_semaphore: asyncio.Semaphore,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    active_pack = await pack_service.get_active_pack(message.from_user.id, message.from_user.username)
    if not active_pack:
        await message.answer("Сначала создайте и активируйте пак через /newpack")
        return

    if message.sticker:
        if message.sticker.is_animated:
            await message.answer("Анимированные .tgs стикеры пока не поддерживаются. Отправьте обычный или video-стикер.")
            return
        if message.sticker.type != StickerType.REGULAR:
            await message.answer("Поддерживаются только обычные стикеры из стикерпаков (regular).")
            return

    incoming = extract_media_from_message(message)
    if incoming is None:
        await message.answer("Не удалось определить формат медиа. Отправьте изображение или видео.")
        return

    user_id = await db.ensure_user(message.from_user.id, message.from_user.username)
    job_dir = settings.temp_dir / str(message.from_user.id) / str(uuid4())
    suffix = Path(incoming.filename or "file.bin").suffix or ".bin"
    input_path = job_dir / f"input{suffix}"

    try:
        await download_file(bot=bot, file_id=incoming.file_id, destination=input_path)
        job = await db.create_media_job(
            user_id=user_id,
            telegram_file_id=incoming.file_id,
            telegram_file_unique_id=incoming.file_unique_id,
            media_kind=incoming.media_kind,
            mime=incoming.mime,
            original_name=incoming.filename,
            original_emoji=incoming.original_emoji,
            temp_path=str(input_path),
        )
    except Exception as exc:
        MediaService.cleanup_job_dir(job_dir)
        await message.answer(f"Не удалось скачать/сохранить медиа: {exc}")
        return

    if incoming.source_is_sticker:
        await message.answer("Анализирую стикер и подбираю эмодзи...")
        try:
            async with media_semaphore:
                processed = await asyncio.to_thread(
                    media_service.process_existing_sticker,
                    input_path,
                    input_path.parent,
                    incoming.media_kind,
                )

            suggestion = await asyncio.to_thread(
                emoji_service.suggest,
                processed.preview_path,
                incoming.media_kind,
                processed.path if incoming.media_kind == MediaKind.VIDEO else None,
            )

            await db.update_media_job_processing(
                job_id=job.id,
                crop_mode=CropMode.FIT,
                processed_path=str(processed.path),
                preview_path=str(processed.preview_path),
                suggestions=suggestion.top3,
            )

            top = suggestion.top3
            text = (
                "Выберите эмодзи для добавляемого стикера:\n"
                f"1) {top[0]}\n"
                f"2) {top[1]}\n"
                f"3) {top[2]}\n"
                f"Авто: {suggestion.auto_pick} (confidence={suggestion.confidence:.2f})"
            )
            if incoming.original_emoji:
                text += f"\nИсходный эмодзи: {incoming.original_emoji}"
            await message.answer(
                text,
                reply_markup=emoji_keyboard(job.id, top, with_original=bool(incoming.original_emoji)),
            )
        except Exception as exc:
            await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
            MediaService.cleanup_job_dir(input_path.parent)
            await message.answer(f"Ошибка обработки стикера: {exc}")
        return

    await message.answer(
        "Выберите режим обработки:",
        reply_markup=crop_keyboard(job.id),
    )


@router.callback_query(F.data.startswith("crop:"))
async def cb_crop_choice(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    media_service: MediaService,
    emoji_service: EmojiService,
    media_semaphore: asyncio.Semaphore,
) -> None:
    if not await ensure_allowed_event(callback, settings):
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"square", "fit"}:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    job_id = int(parts[1])
    crop_mode = CropMode(parts[2])
    user_id = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    job = await db.get_media_job(job_id=job_id, user_id=user_id)
    if not job:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    input_path = Path(job.temp_path)
    if not input_path.exists():
        await db.set_media_job_status(job.id, JobStatus.ERROR, "Исходный файл не найден")
        await callback.answer("Файл недоступен", show_alert=True)
        return

    await callback.answer("Обрабатываю...")
    if callback.message:
        await callback.message.edit_text("Конвертирую медиа, это может занять до 20 секунд...")

    try:
        async with media_semaphore:
            if job.media_kind == MediaKind.IMAGE:
                processed = await asyncio.to_thread(media_service.process_image, input_path, input_path.parent, crop_mode)
            else:
                processed = await asyncio.to_thread(media_service.process_video, input_path, input_path.parent, crop_mode)

        suggestion = await asyncio.to_thread(
            emoji_service.suggest,
            processed.preview_path,
            job.media_kind,
            processed.path if job.media_kind == MediaKind.VIDEO else None,
        )

        await db.update_media_job_processing(
            job_id=job.id,
            crop_mode=crop_mode,
            processed_path=str(processed.path),
            preview_path=str(processed.preview_path),
            suggestions=suggestion.top3,
        )

        top = suggestion.top3
        text = (
            "Готово. Выберите эмодзи для стикера:\n"
            f"1) {top[0]}\n"
            f"2) {top[1]}\n"
            f"3) {top[2]}\n"
            f"Авто: {suggestion.auto_pick} (confidence={suggestion.confidence:.2f})"
        )

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=emoji_keyboard(job.id, top, with_original=bool(job.original_emoji)),
            )
    except Exception as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(input_path.parent)
        if callback.message:
            await callback.message.edit_text(f"Ошибка обработки: {exc}")


@router.callback_query(F.data.startswith("emoji:"))
async def cb_emoji_choice(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    pack_service: PackService,
) -> None:
    if not await ensure_allowed_event(callback, settings):
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return

    job_id = int(parts[1])
    choice = parts[2]
    if choice not in {"auto", "0", "1", "2", "original"}:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    user_id = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    job = await db.get_media_job(job_id=job_id, user_id=user_id)
    if not job:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    if not job.processed_path:
        await callback.answer("Медиа еще не обработано", show_alert=True)
        return

    suggestions: list[str] = ["🖼️", "✨", "🔥"]
    if job.suggestions_json:
        try:
            parsed = json.loads(job.suggestions_json)
            if isinstance(parsed, list) and parsed:
                suggestions = [str(x) for x in parsed][:3]
                while len(suggestions) < 3:
                    suggestions.append("✨")
        except json.JSONDecodeError:
            pass

    if choice == "original":
        emoji = job.original_emoji or suggestions[0]
    else:
        emoji = suggestions[0] if choice == "auto" else suggestions[int(choice)]

    await callback.answer("Добавляю в стикерпак...")
    if callback.message:
        await callback.message.edit_text("Отправляю стикер в Telegram...")

    processed_path = Path(job.processed_path)
    try:
        pack = await pack_service.add_processed_sticker(
            tg_user_id=callback.from_user.id,
            media_kind=job.media_kind,
            sticker_path=processed_path,
            emoji=emoji,
            username=callback.from_user.username,
        )

        source_hash = _hash_file(processed_path)
        await db.add_sticker_record(
            pack_id=pack.id,
            media_kind=job.media_kind,
            emoji=emoji,
            telegram_file_id=None,
            source_hash=source_hash,
        )
        await db.set_media_job_status(job.id, JobStatus.DONE)
        MediaService.cleanup_job_dir(processed_path.parent)

        link = f"https://t.me/addstickers/{pack.tg_set_name}" if pack.tg_set_name else ""
        text = f"Стикер добавлен в пак «{pack.title}».\nЭмодзи: {emoji}"
        if link:
            text += f"\nПак: {link}"
        if callback.message:
            await callback.message.edit_text(text)
    except TelegramBadRequest as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(processed_path.parent)
        hint = "Telegram отклонил формат для этого пака. Выберите другой пак через /packs или создайте новый /newpack."
        if callback.message:
            await callback.message.edit_text(f"Ошибка Telegram: {exc}\n{hint}")
    except Exception as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(processed_path.parent)
        if callback.message:
            await callback.message.edit_text(f"Ошибка добавления стикера: {exc}")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
