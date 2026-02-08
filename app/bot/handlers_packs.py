from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import ensure_allowed_event
from app.bot.keyboards import packs_keyboard
from app.bot.states import PackStates
from app.config import Settings
from app.services.pack_service import PackService

router = Router(name="packs")


@router.message(Command("newpack"))
async def cmd_newpack(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.set_state(PackStates.waiting_for_pack_title)
    await message.answer("Введите название нового стикерпака.")


@router.message(PackStates.waiting_for_pack_title, F.text, ~F.text.startswith("/"))
async def receive_pack_title(
    message: Message,
    state: FSMContext,
    settings: Settings,
    pack_service: PackService,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым. Попробуйте еще раз.")
        return

    pack = await pack_service.create_draft_pack(message.from_user.id, title)
    await state.clear()
    await message.answer(
        f"Пак создан как черновик и активирован.\n"
        f"ID: {pack.id}\n"
        f"Название: {pack.title}\n"
        "Теперь отправьте фото или видео для первого стикера."
    )


@router.message(Command("packs"))
async def cmd_packs(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    packs = await pack_service.list_packs(message.from_user.id)
    if not packs:
        await message.answer("Паков пока нет. Создайте первый через /newpack")
        return
    await message.answer("Ваши паки:", reply_markup=packs_keyboard(packs))


@router.message(Command("setactive"))
async def cmd_set_active(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /setactive [pack_id]")
        return
    pack_id = int(parts[1])
    ok = await pack_service.activate_pack(message.from_user.id, pack_id)
    if not ok:
        await message.answer("Пак не найден.")
        return
    await message.answer(f"Активный пак переключен на ID {pack_id}.")


@router.callback_query(F.data.startswith("pack:"))
async def cb_activate_pack(callback: CallbackQuery, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(callback, settings):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[2] != "activate" or not parts[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return

    pack_id = int(parts[1])
    ok = await pack_service.activate_pack(callback.from_user.id, pack_id)
    if not ok:
        await callback.answer("Пак не найден", show_alert=True)
        return

    await callback.answer("Активный пак обновлен")
    packs = await pack_service.list_packs(callback.from_user.id)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=packs_keyboard(packs))
