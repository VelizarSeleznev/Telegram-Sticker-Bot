from __future__ import annotations

from aiogram import F, Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.helpers import ensure_allowed_event
from app.bot.keyboards import (
    BTN_ACTIVE,
    BTN_CANCEL,
    BTN_HELP,
    BTN_INVITE,
    BTN_MEMBERS,
    BTN_NEW_PACK,
    BTN_PACKS,
    cancel_keyboard,
    main_menu_keyboard,
    packs_keyboard,
)
from app.bot.states import InviteStates, PackStates
from app.config import Settings
from app.services.collab_service import CollabService
from app.services.pack_service import PackService

router = Router(name="menu")


@router.message(Command("menu"))
async def cmd_menu(message: Message, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await message.answer("Меню показано.", reply_markup=main_menu_keyboard())


@router.message(F.text == BTN_NEW_PACK)
async def menu_new_pack(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.set_state(PackStates.waiting_for_pack_title)
    await message.answer("Введите название нового стикерпака.", reply_markup=cancel_keyboard())


@router.message(F.text == BTN_PACKS)
async def menu_packs(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    packs = await pack_service.list_packs(message.from_user.id, message.from_user.username)
    if not packs:
        await message.answer("Паков пока нет. Создайте первый через «Новый пак».", reply_markup=main_menu_keyboard())
        return
    # Inline keyboard for packs doesn't remove the reply keyboard; persistent menu stays visible.
    await message.answer("Ваши и совместные паки:", reply_markup=packs_keyboard(packs))


@router.message(F.text == BTN_ACTIVE)
async def menu_active(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    active = await pack_service.get_active_pack(message.from_user.id, message.from_user.username)
    if not active:
        await message.answer("Активный пак не выбран. Нажмите «Новый пак» или «Пакеты».", reply_markup=main_menu_keyboard())
        return

    status = "черновик" if active.status.value == "draft" else "готов"
    text = f"Активный пак: «{active.title}»\nID: {active.id}\nСтатус: {status}\nРоль: {active.role.value}"
    if active.tg_set_name and active.status.value == "ready":
        text += f"\nПак: https://t.me/addstickers/{active.tg_set_name}"
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(F.text == BTN_INVITE)
async def menu_invite_start(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.set_state(InviteStates.waiting_for_invite_username)
    await message.answer("Введите @username, которого хотите пригласить.", reply_markup=cancel_keyboard())


@router.message(InviteStates.waiting_for_invite_username, F.text, ~F.text.startswith("/"))
async def invite_receive_username(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    collab_service: CollabService,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    raw = (message.text or "").strip()
    if raw == BTN_CANCEL:
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_keyboard())
        return

    if not raw:
        await message.answer("Введите @username или нажмите «Отмена».", reply_markup=cancel_keyboard())
        return

    invited_username_raw = raw if raw.startswith("@") else f"@{raw}"
    try:
        pack, invitation, invite_link, invited_tg_user_id = await collab_service.create_invitation_for_active_pack(
            requester_tg_user_id=message.from_user.id,
            requester_username=message.from_user.username,
            invited_username_raw=invited_username_raw,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось создать инвайт: {exc}", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    inviter = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    dm_sent = False
    if invited_tg_user_id is not None:
        try:
            await bot.send_message(
                chat_id=invited_tg_user_id,
                text=(
                    f"Вас пригласили редактировать стикерпак «{pack.title}».\n"
                    f"Пригласил: {inviter}\n"
                    f"Ссылка для принятия: {invite_link}"
                ),
            )
            dm_sent = True
        except Exception:  # noqa: BLE001
            dm_sent = False

    dm_note = "\nИнвайт отправлен в личные сообщения." if dm_sent else "\nЛС недоступны — передайте ссылку вручную."
    await message.answer(
        f"Инвайт создан для {invitation.invited_username_lc}."
        f"\nСрок: 24 часа"
        f"\nСсылка: {invite_link}"
        f"{dm_note}",
        reply_markup=main_menu_keyboard(),
    )
    await state.clear()


@router.message(F.text == BTN_MEMBERS)
async def menu_members(message: Message, settings: Settings, collab_service: CollabService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    # Reuse the same text output as /members handler.
    try:
        pack, role, members, pending = await collab_service.members_for_active_pack(
            requester_tg_user_id=message.from_user.id,
            requester_username=message.from_user.username,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(str(exc), reply_markup=main_menu_keyboard())
        return

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    lines = [f"Пак: {pack.title} (ID: {pack.id})", f"Ваша роль: {role.value}", "", "Участники:"]
    for member in members:
        username = f"@{member.username_lc}" if member.username_lc else "(без username)"
        lines.append(f"- id={member.user_id} role={member.role.value} tg={member.tg_user_id} {username}")

    lines.append("")
    lines.append("Pending-инвайты:")
    if not pending:
        lines.append("- нет")
    else:
        for inv in pending:
            remain = inv.expires_at - now
            hours = max(0, int(remain.total_seconds() // 3600))
            lines.append(f"- @{inv.invited_username_lc}, истекает через ~{hours}ч")

    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


@router.message(F.text == BTN_HELP)
async def menu_help(message: Message, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await message.answer(
        "Поддержка форматов (best-effort):\n"
        "Изображения: jpg/jpeg/png/webp/heic\n"
        "Видео: mp4/mov/webm/gif\n\n"
        "Режимы:\n"
        "- Квадрат: center crop + 512x512\n"
        "- Без обрезки: fit в 512x512\n\n"
        "Видео автоматически режется до 3 секунд и сжимается под лимит Telegram.\n"
        "Можно приглашать редакторов в активный пак через /invite @username.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == BTN_CANCEL)
async def menu_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu_keyboard())

