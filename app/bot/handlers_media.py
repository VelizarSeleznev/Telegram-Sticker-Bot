from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from aiogram import F, Bot, Router
from aiogram.enums import StickerType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.bot.helpers import download_file, ensure_allowed_event, extract_media_from_message
from app.bot.keyboards import emoji_keyboard, sticker_delete_keyboard, sticker_delete_or_import_keyboard
from app.bot.states import EmojiStates
from app.config import Settings
from app.db.models import CropMode, JobStatus, MediaKind, StickerActionKind
from app.db.repo import Database
from app.services.emoji_service import EmojiService
from app.services.media_service import MediaService
from app.services.pack_service import PackService
from app.services.telegram_sticker_api import TelegramStickerApi

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
    tg_api: TelegramStickerApi,
    state: FSMContext,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    # Stickers can be deleted from our packs even if the user has no active pack.
    if message.sticker:
        if message.sticker.is_animated:
            await message.answer("Анимированные .tgs стикеры пока не поддерживаются. Отправьте обычный или video-стикер.")
            return
        if message.sticker.type != StickerType.REGULAR:
            await message.answer("Поддерживаются только обычные стикеры из стикерпаков (regular).")
            return

        user_id = await db.ensure_user(message.from_user.id, message.from_user.username)
        set_name = message.sticker.set_name
        if set_name:
            source_pack = await db.get_pack_by_tg_set_name_for_user(set_name, user_id)
        else:
            source_pack = None

        if source_pack:
            now = datetime.now(timezone.utc)
            expires_at = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
            delete_token = await db.create_sticker_action(
                user_id=user_id,
                kind=StickerActionKind.DELETE,
                sticker_file_id=message.sticker.file_id,
                sticker_file_unique_id=message.sticker.file_unique_id,
                sticker_set_name=set_name,
                original_emoji=message.sticker.emoji,
                expires_at=expires_at,
            )

            active_pack = await pack_service.get_active_pack(message.from_user.id, message.from_user.username)
            import_token = None
            if active_pack and active_pack.id != source_pack.id:
                import_token = await db.create_sticker_action(
                    user_id=user_id,
                    kind=StickerActionKind.IMPORT,
                    sticker_file_id=message.sticker.file_id,
                    sticker_file_unique_id=message.sticker.file_unique_id,
                    sticker_set_name=set_name,
                    original_emoji=message.sticker.emoji,
                    expires_at=expires_at,
                )

            if active_pack and active_pack.id != source_pack.id and import_token:
                await message.answer(
                    f"Стикер из вашего пака «{source_pack.title}». Что сделать?",
                    reply_markup=sticker_delete_or_import_keyboard(
                        delete_token=delete_token,
                        import_token=import_token,
                        source_title=source_pack.title,
                        active_title=active_pack.title,
                    ),
                )
            else:
                await message.answer(
                    f"Вы хотите удалить этот стикер из пака «{source_pack.title}»?",
                    reply_markup=sticker_delete_keyboard(delete_token, source_pack.title),
                )
            return

        # Not our pack (or no access) -> fall through to import flow below.

    active_pack = await pack_service.get_active_pack(message.from_user.id, message.from_user.username)
    if not active_pack:
        await message.answer("Сначала создайте и активируйте пак через /newpack")
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
            text = _emoji_choice_text(
                title="Выберите эмодзи для добавляемого стикера:",
                top=top,
                auto_pick=suggestion.auto_pick,
                confidence=suggestion.confidence,
            )
            if incoming.original_emoji:
                text += f"\nИсходный эмодзи: {incoming.original_emoji}"
            await _set_direct_emoji_state(state, job.id)
            await message.answer(
                text,
                reply_markup=emoji_keyboard(job.id, top, with_original=bool(incoming.original_emoji)),
            )
        except Exception as exc:
            await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
            MediaService.cleanup_job_dir(input_path.parent)
            await message.answer(f"Ошибка обработки стикера: {exc}")
        return

    await message.answer("Конвертирую медиа без обрезки, это может занять до 20 секунд...")
    try:
        async with media_semaphore:
            if incoming.media_kind == MediaKind.IMAGE:
                processed = await asyncio.to_thread(
                    media_service.process_image,
                    input_path,
                    input_path.parent,
                    CropMode.FIT,
                )
            else:
                processed = await asyncio.to_thread(
                    media_service.process_video,
                    input_path,
                    input_path.parent,
                    CropMode.FIT,
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
        await _set_direct_emoji_state(state, job.id)
        await message.answer(
            _emoji_choice_text(
                title="Готово. Выберите эмодзи для стикера:",
                top=top,
                auto_pick=suggestion.auto_pick,
                confidence=suggestion.confidence,
            ),
            reply_markup=emoji_keyboard(job.id, top, with_original=bool(job.original_emoji), crop_mode=CropMode.FIT),
        )
    except Exception as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(input_path.parent)
        await message.answer(f"Ошибка обработки: {exc}")


@router.callback_query(F.data.startswith("stact:"))
async def cb_sticker_action(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    db: Database,
    tg_api: TelegramStickerApi,
    pack_service: PackService,
    media_service: MediaService,
    emoji_service: EmojiService,
    media_semaphore: asyncio.Semaphore,
    state: FSMContext,
) -> None:
    if not await ensure_allowed_event(callback, settings):
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    token = parts[1]
    op = parts[2]
    if op not in {"delete_confirm", "import", "cancel"}:
        await callback.answer("Некорректная операция", show_alert=True)
        return

    user_id = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    action = await db.get_sticker_action(token, user_id)
    if not action:
        await callback.answer("Кнопка устарела. Пришлите стикер заново.", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    if action.expires_at <= now:
        await db.delete_sticker_actions_for_unique_id(user_id, action.sticker_file_unique_id)
        await callback.answer("Кнопка устарела. Пришлите стикер заново.", show_alert=True)
        return

    if op == "cancel":
        await db.delete_sticker_actions_for_unique_id(user_id, action.sticker_file_unique_id)
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.edit_text("Отменено.")
        return

    if op == "delete_confirm":
        if action.kind != StickerActionKind.DELETE:
            await callback.answer("Кнопка устарела. Пришлите стикер заново.", show_alert=True)
            return

        pack_title = None
        if action.sticker_set_name:
            pack = await db.get_pack_by_tg_set_name_for_user(action.sticker_set_name, user_id)
            if not pack:
                await callback.answer("У вас больше нет доступа к этому паку.", show_alert=True)
                return
            pack_title = pack.title

        await callback.answer("Удаляю...")
        try:
            await tg_api.delete_sticker(sticker_file_id=action.sticker_file_id)
            await db.delete_sticker_actions_for_unique_id(user_id, action.sticker_file_unique_id)
            if callback.message:
                if pack_title:
                    await callback.message.edit_text(f"Удалено из пака «{pack_title}».")
                else:
                    await callback.message.edit_text("Удалено.")
        except TelegramBadRequest as exc:
            await db.delete_sticker_actions_for_unique_id(user_id, action.sticker_file_unique_id)
            hint = "Удаление возможно только из стикерпаков, созданных этим ботом."
            if callback.message:
                await callback.message.edit_text(f"Ошибка Telegram: {exc}\n{hint}")
        except Exception as exc:  # noqa: BLE001
            if callback.message:
                await callback.message.edit_text(f"Ошибка удаления: {exc}")
        return

    # import
    if action.kind != StickerActionKind.IMPORT:
        await callback.answer("Кнопка устарела. Пришлите стикер заново.", show_alert=True)
        return

    active_pack = await pack_service.get_active_pack(callback.from_user.id, callback.from_user.username)
    if not active_pack:
        await callback.answer("Нет активного пака", show_alert=True)
        if callback.message:
            await callback.message.edit_text("Сначала создайте и активируйте пак через /newpack")
        return

    await callback.answer("Импортирую...")
    job_dir = settings.temp_dir / str(callback.from_user.id) / str(uuid4())
    try:
        tg_file = await bot.get_file(action.sticker_file_id)
        suffix = Path(tg_file.file_path or "sticker.bin").suffix or ".bin"
        input_path = job_dir / f"sticker{suffix}"
        await download_file(bot=bot, file_id=action.sticker_file_id, destination=input_path)

        file_path_lc = (tg_file.file_path or "").lower()
        media_kind = MediaKind.VIDEO if file_path_lc.endswith(".webm") else MediaKind.IMAGE

        job = await db.create_media_job(
            user_id=user_id,
            telegram_file_id=action.sticker_file_id,
            telegram_file_unique_id=action.sticker_file_unique_id,
            media_kind=media_kind,
            mime="video/webm" if media_kind == MediaKind.VIDEO else "image/webp",
            original_name=input_path.name,
            original_emoji=action.original_emoji,
            temp_path=str(input_path),
        )

        async with media_semaphore:
            processed = await asyncio.to_thread(
                media_service.process_existing_sticker,
                input_path,
                input_path.parent,
                media_kind,
            )

        suggestion = await asyncio.to_thread(
            emoji_service.suggest,
            processed.preview_path,
            media_kind,
            processed.path if media_kind == MediaKind.VIDEO else None,
        )

        await db.update_media_job_processing(
            job_id=job.id,
            crop_mode=CropMode.FIT,
            processed_path=str(processed.path),
            preview_path=str(processed.preview_path),
            suggestions=suggestion.top3,
        )

        await db.delete_sticker_actions_for_unique_id(user_id, action.sticker_file_unique_id)

        top = suggestion.top3
        text = _emoji_choice_text(
            title="Выберите эмодзи для добавляемого стикера:",
            top=top,
            auto_pick=suggestion.auto_pick,
            confidence=suggestion.confidence,
        )
        if action.original_emoji:
            text += f"\nИсходный эмодзи: {action.original_emoji}"

        if callback.message:
            await _set_direct_emoji_state(state, job.id)
            await callback.message.edit_text(
                text,
                reply_markup=emoji_keyboard(job.id, top, with_original=bool(action.original_emoji)),
            )
    except Exception as exc:  # noqa: BLE001
        MediaService.cleanup_job_dir(job_dir)
        if callback.message:
            await callback.message.edit_text(f"Ошибка импорта: {exc}")


@router.callback_query(F.data.startswith("crop:"))
async def cb_crop_choice(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    media_service: MediaService,
    emoji_service: EmojiService,
    media_semaphore: asyncio.Semaphore,
    state: FSMContext,
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
        text = _emoji_choice_text(
            title="Готово. Выберите эмодзи для стикера:",
            top=top,
            auto_pick=suggestion.auto_pick,
            confidence=suggestion.confidence,
        )

        if callback.message:
            await _set_direct_emoji_state(state, job.id)
            await callback.message.edit_text(
                text,
                reply_markup=emoji_keyboard(job.id, top, with_original=bool(job.original_emoji), crop_mode=crop_mode),
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
    state: FSMContext,
) -> None:
    if not await ensure_allowed_event(callback, settings):
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return

    job_id = int(parts[1])
    choice = parts[2]
    if choice not in {"auto", "0", "1", "2", "original", "custom"}:
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

    if choice == "custom":
        await state.set_state(EmojiStates.waiting_for_custom_emoji)
        await state.update_data(custom_emoji_job_id=job.id)
        await callback.answer("Введите свой эмодзи")
        if callback.message:
            await callback.message.edit_text("Отправьте эмодзи одним сообщением. Например: 😎")
        return

    if choice == "original":
        emoji = job.original_emoji or suggestions[0]
    else:
        emoji = suggestions[0] if choice == "auto" else suggestions[int(choice)]

    try:
        await callback.answer("Добавляю в стикерпак...")
        if callback.message:
            await callback.message.edit_text("Отправляю стикер в Telegram...")
        text = await _add_job_with_emoji(
            db=db,
            pack_service=pack_service,
            reply_message=callback.message,
            tg_user_id=callback.from_user.id,
            username=callback.from_user.username,
            job=job,
            emoji=emoji,
        )
        if callback.message:
            await callback.message.edit_text(text)
        await state.clear()
    except TelegramBadRequest as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(Path(job.processed_path).parent)
        hint = "Telegram отклонил формат для этого пака. Выберите другой пак через /packs или создайте новый /newpack."
        if callback.message:
            await callback.message.edit_text(f"Ошибка Telegram: {exc}\n{hint}")
    except Exception as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(Path(job.processed_path).parent)
        if callback.message:
            await callback.message.edit_text(f"Ошибка добавления стикера: {exc}")


@router.message(EmojiStates.waiting_for_custom_emoji, F.text, ~F.text.startswith("/"))
async def receive_custom_emoji(
    message: Message,
    settings: Settings,
    state: FSMContext,
    db: Database,
    pack_service: PackService,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    data = await state.get_data()
    job_id = data.get("custom_emoji_job_id")
    if not isinstance(job_id, int):
        await state.clear()
        await message.answer("Сессия выбора эмодзи устарела. Отправьте медиа заново.")
        return

    user_id = await db.ensure_user(message.from_user.id, message.from_user.username)
    job = await db.get_media_job(job_id=job_id, user_id=user_id)
    if not job or not job.processed_path:
        await state.clear()
        await message.answer("Медиа не найдено. Отправьте медиа заново.")
        return

    custom_emoji = _normalize_custom_emoji(message.text)
    if not custom_emoji:
        await message.answer("Нужен один эмодзи без пробелов. Пример: 😎")
        return

    try:
        await message.answer("Добавляю в стикерпак...")
        text = await _add_job_with_emoji(
            db=db,
            pack_service=pack_service,
            reply_message=message,
            tg_user_id=message.from_user.id,
            username=message.from_user.username,
            job=job,
            emoji=custom_emoji,
        )
        await state.clear()
        await message.answer(text)
    except TelegramBadRequest as exc:
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(Path(job.processed_path).parent)
        hint = "Telegram отклонил эмодзи или формат. Попробуйте другой эмодзи или отправьте медиа заново."
        await message.answer(f"Ошибка Telegram: {exc}\n{hint}")
    except Exception as exc:  # noqa: BLE001
        await db.set_media_job_status(job.id, JobStatus.ERROR, str(exc))
        MediaService.cleanup_job_dir(Path(job.processed_path).parent)
        await message.answer(f"Ошибка добавления стикера: {exc}")


async def _set_direct_emoji_state(state: FSMContext, job_id: int) -> None:
    await state.set_state(EmojiStates.waiting_for_custom_emoji)
    await state.update_data(custom_emoji_job_id=job_id)


def _emoji_choice_text(*, title: str, top: list[str], auto_pick: str, confidence: float) -> str:
    return (
        f"{title}\n"
        f"1) {top[0]}\n"
        f"2) {top[1]}\n"
        f"3) {top[2]}\n"
        f"Авто: {auto_pick} (confidence={confidence:.2f})\n\n"
        "Можно просто отправить нужный эмодзи сообщением."
    )


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _normalize_custom_emoji(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    if len(text) > 16:
        return None
    return text


async def _add_job_with_emoji(
    *,
    db: Database,
    pack_service: PackService,
    reply_message: Message | None,
    tg_user_id: int,
    username: str | None,
    job,
    emoji: str,
) -> str:
    processed_path = Path(job.processed_path)
    pack = await pack_service.add_processed_sticker(
        tg_user_id=tg_user_id,
        media_kind=job.media_kind,
        sticker_path=processed_path,
        emoji=emoji,
        username=username,
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
    preview_sent = await _send_added_sticker_preview(reply_message, processed_path)
    MediaService.cleanup_job_dir(processed_path.parent)

    link = f"https://t.me/addstickers/{pack.tg_set_name}" if pack.tg_set_name else ""
    text = f"Стикер добавлен в пак «{pack.title}».\nЭмодзи: {emoji}"
    if link:
        text += f"\nПак: {link}"
    if not preview_sent:
        text += "\nПревью не удалось отправить, но стикер уже добавлен в пак."
    return text


async def _send_added_sticker_preview(message: Message | None, sticker_path: Path) -> bool:
    if message is None:
        return False
    try:
        await message.answer_sticker(sticker=FSInputFile(sticker_path))
        return True
    except TelegramBadRequest:
        return False
