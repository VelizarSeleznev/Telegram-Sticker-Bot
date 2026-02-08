from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import ensure_allowed_event
from app.bot.keyboards import packs_keyboard
from app.bot.states import PackStates
from app.config import Settings
from app.services.collab_service import CollabService
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

    pack = await pack_service.create_draft_pack(message.from_user.id, title, message.from_user.username)
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
    packs = await pack_service.list_packs(message.from_user.id, message.from_user.username)
    if not packs:
        await message.answer("Паков пока нет. Создайте первый через /newpack")
        return
    await message.answer("Ваши и совместные паки:", reply_markup=packs_keyboard(packs))


@router.message(Command("setactive"))
async def cmd_set_active(message: Message, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /setactive [pack_id]")
        return
    pack_id = int(parts[1])
    ok = await pack_service.activate_pack(message.from_user.id, pack_id, message.from_user.username)
    if not ok:
        await message.answer("Пак не найден или у вас нет доступа.")
        return
    await message.answer(f"Активный пак переключен на ID {pack_id}.")


@router.message(Command("invite"))
async def cmd_invite(
    message: Message,
    bot: Bot,
    settings: Settings,
    collab_service: CollabService,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().startswith("@"):
        await message.answer("Формат: /invite @username")
        return

    invited_username_raw = parts[1].strip()
    try:
        pack, invitation, invite_link, invited_tg_user_id = await collab_service.create_invitation_for_active_pack(
            requester_tg_user_id=message.from_user.id,
            requester_username=message.from_user.username,
            invited_username_raw=invited_username_raw,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось создать инвайт: {exc}")
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
        f"{dm_note}"
    )


@router.message(Command("members"))
async def cmd_members(message: Message, settings: Settings, collab_service: CollabService) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    try:
        pack, role, members, pending = await collab_service.members_for_active_pack(
            requester_tg_user_id=message.from_user.id,
            requester_username=message.from_user.username,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(str(exc))
        return

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

    await message.answer("\n".join(lines))


@router.message(Command("kick"))
async def cmd_kick(message: Message, settings: Settings, collab_service: CollabService) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /kick [member_id]")
        return

    target_member_id = int(parts[1])
    try:
        pack, target = await collab_service.kick_member_from_active_pack(
            requester_tg_user_id=message.from_user.id,
            requester_username=message.from_user.username,
            target_member_id=target_member_id,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось удалить участника: {exc}")
        return

    target_name = f"@{target.username_lc}" if target.username_lc else f"tg={target.tg_user_id}"
    await message.answer(f"Участник {target_name} удален из пака «{pack.title}».")


@router.callback_query(F.data.startswith("pack:"))
async def cb_activate_pack(callback: CallbackQuery, settings: Settings, pack_service: PackService) -> None:
    if not await ensure_allowed_event(callback, settings):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[2] != "activate" or not parts[1].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return

    pack_id = int(parts[1])
    ok = await pack_service.activate_pack(callback.from_user.id, pack_id, callback.from_user.username)
    if not ok:
        await callback.answer("Пак не найден или нет доступа", show_alert=True)
        return

    await callback.answer("Активный пак обновлен")
    packs = await pack_service.list_packs(callback.from_user.id, callback.from_user.username)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=packs_keyboard(packs))
