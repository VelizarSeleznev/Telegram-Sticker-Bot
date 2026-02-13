from __future__ import annotations

from aiogram import F, Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import ensure_allowed_event
from app.bot.invite_notifications import format_actor, notify_inviter_about_decision
from app.bot.keyboards import main_menu_keyboard
from app.config import Settings
from app.db.repo import Database
from app.services.collab_service import CollabService
from app.services.pack_service import PackService

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    pack_service: PackService,
    collab_service: CollabService,
) -> None:
    if not await ensure_allowed_event(message, settings):
        return

    await collab_service.touch_user(message.from_user.id, message.from_user.username)

    deep_link_result = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("join_"):
        token = parts[1][len("join_") :].strip()
        if token:
            try:
                invitation = await db.get_invitation_by_token(token)
                joined_pack = await collab_service.accept_invitation_by_token(
                    token=token,
                    accepter_tg_user_id=message.from_user.id,
                    accepter_username=message.from_user.username,
                )
                deep_link_result = f"Инвайт принят. Пак «{joined_pack.title}» теперь доступен вам как редактор."
                if invitation:
                    inviter_tg_user_id = await db.get_tg_user_id_by_user_id(invitation.inviter_user_id)
                    if inviter_tg_user_id:
                        await notify_inviter_about_decision(
                            bot=bot,
                            inviter_tg_user_id=inviter_tg_user_id,
                            invitee_display=format_actor(message.from_user.username, message.from_user.id),
                            pack_title=joined_pack.title,
                            accepted=True,
                        )
            except Exception as exc:  # noqa: BLE001
                deep_link_result = f"Не удалось принять инвайт: {exc}"

    active = await pack_service.get_active_pack(message.from_user.id, message.from_user.username)
    if active:
        status = "черновик" if active.status.value == "draft" else "готов"
        active_text = f"Активный пак: {active.title} (ID: {active.id}, {status}, роль: {active.role.value})"
    else:
        active_text = "Активный пак не выбран. Создайте через /newpack или выберите через /packs"

    intro = "Привет. Я помогу делать стикерпаки (фото и видео)."
    if deep_link_result:
        intro = f"{intro}\n{deep_link_result}"

    await message.answer(
        f"{intro}\n"
        f"{active_text}\n\n"
        "Команды:\n"
        "/newpack - создать новый пак\n"
        "/packs - список моих и shared паков\n"
        "/setactive [id] - выбрать активный\n"
        "/invite @username - пригласить редактора в активный пак\n"
        "/members - участники активного пака\n"
        "/kick [member_id] - удалить участника (только owner)\n"
        "/help - помощь\n"
        "Отправьте медиа, и я предложу обрезку и эмодзи.",
        reply_markup=main_menu_keyboard(),
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
        "Видео автоматически режется до 3 секунд и сжимается под лимит Telegram.\n"
        "Можно приглашать редакторов в активный пак через /invite @username.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_allowed_event(message, settings):
        return
    await state.clear()
    await message.answer("Текущая операция отменена.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("inv:"))
async def cb_invitation_decision(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    db: Database,
    collab_service: CollabService,
) -> None:
    if not await ensure_allowed_event(callback, settings):
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[2] not in {"accept", "decline"}:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    token = parts[1]
    action = parts[2]
    invitation = await db.get_invitation_by_token(token)
    if not invitation:
        await callback.answer("Инвайт не найден или уже недействителен", show_alert=True)
        if callback.message:
            await callback.message.edit_text("Инвайт недействителен.")
        return

    try:
        inviter_tg_user_id = await db.get_tg_user_id_by_user_id(invitation.inviter_user_id)
        invitee_display = format_actor(callback.from_user.username, callback.from_user.id)

        if action == "accept":
            joined_pack = await collab_service.accept_invitation_by_token(
                token=token,
                accepter_tg_user_id=callback.from_user.id,
                accepter_username=callback.from_user.username,
            )
            if inviter_tg_user_id:
                await notify_inviter_about_decision(
                    bot=bot,
                    inviter_tg_user_id=inviter_tg_user_id,
                    invitee_display=invitee_display,
                    pack_title=joined_pack.title,
                    accepted=True,
                )
            await callback.answer("Инвайт принят")
            if callback.message:
                await callback.message.edit_text(f"✅ Инвайт принят. Пак «{joined_pack.title}» теперь доступен вам.")
            return

        declined = await collab_service.decline_invitation_by_token(
            token=token,
            decliner_tg_user_id=callback.from_user.id,
            decliner_username=callback.from_user.username,
        )
        if inviter_tg_user_id:
            await notify_inviter_about_decision(
                bot=bot,
                inviter_tg_user_id=inviter_tg_user_id,
                invitee_display=invitee_display,
                pack_title=declined.pack_title or "без названия",
                accepted=False,
            )
        await callback.answer("Инвайт отклонен")
        if callback.message:
            await callback.message.edit_text("❌ Инвайт отклонен.")
    except Exception as exc:  # noqa: BLE001
        await callback.answer(str(exc), show_alert=True)
        if callback.message:
            await callback.message.edit_text(f"Не удалось обработать инвайт: {exc}")
