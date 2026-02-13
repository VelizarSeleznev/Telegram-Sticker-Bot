from __future__ import annotations

from aiogram import Bot

from app.bot.keyboards import invite_decision_keyboard


def format_actor(username: str | None, tg_user_id: int) -> str:
    return f"@{username}" if username else f"id={tg_user_id}"


async def send_invitation_prompt(
    *,
    bot: Bot,
    invited_tg_user_id: int,
    pack_title: str,
    inviter_display: str,
    invite_link: str,
    token: str,
) -> bool:
    try:
        await bot.send_message(
            chat_id=invited_tg_user_id,
            text=(
                f"{inviter_display} пригласил(а) вас редактировать стикерпак «{pack_title}».\n"
                f"Принять приглашение?\nЕсли кнопки не сработают: {invite_link}"
            ),
            reply_markup=invite_decision_keyboard(token),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


async def notify_inviter_about_decision(
    *,
    bot: Bot,
    inviter_tg_user_id: int,
    invitee_display: str,
    pack_title: str,
    accepted: bool,
) -> None:
    decision = "принял(а)" if accepted else "отклонил(а)"
    try:
        await bot.send_message(
            chat_id=inviter_tg_user_id,
            text=f"{invitee_display} {decision} инвайт в пак «{pack_title}».",
        )
    except Exception:  # noqa: BLE001
        pass
