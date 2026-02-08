from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.services.pack_service import PackService
from app.bot.helpers import ensure_allowed_event

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    active = await pack_service.get_active_pack(message.from_user.id)
    if active:
        status = "черновик" if active.status.value == "draft" else "готов"
        active_text = f"Активный пак: {active.title} (ID: {active.id}, {status})"
    else:
        active_text = "Активный пак не выбран. Создайте через /newpack"

    await message.answer(
        "Привет. Я помогу делать стикерпаки (фото и видео).\n"
        f"{active_text}\n\n"
        "Команды:\n"
        "/newpack - создать новый пак\n"
        "/packs - список паков\n"
        "/setactive [id] - выбрать активный\n"
        "/help - помощь\n"
        "Отправьте медиа, и я предложу обрезку и эмодзи."
    )


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await message.answer(
        "Поддержка форматов (best-effort):\n"
        "Изображения: jpg/jpeg/png/webp/heic\n"
        "Видео: mp4/mov/webm/gif\n\n"
        "Режимы:\n"
        "- Квадрат: center crop + 512x512\n"
        "- Без обрезки: fit в 512x512\n\n"
        "Видео автоматически режется до 3 секунд и сжимается под лимит Telegram."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.clear()
    await message.answer("Текущая операция отменена.")
