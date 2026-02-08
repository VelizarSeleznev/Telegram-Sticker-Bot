from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Pack


def crop_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Обрезать до квадрата", callback_data=f"crop:{job_id}:square"),
                InlineKeyboardButton(text="Без обрезки", callback_data=f"crop:{job_id}:fit"),
            ]
        ]
    )


def emoji_keyboard(job_id: int, top3: list[str], with_original: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=f"{top3[0]}", callback_data=f"emoji:{job_id}:0"),
            InlineKeyboardButton(text=f"{top3[1]}", callback_data=f"emoji:{job_id}:1"),
            InlineKeyboardButton(text=f"{top3[2]}", callback_data=f"emoji:{job_id}:2"),
        ],
        [InlineKeyboardButton(text="Оставить авто", callback_data=f"emoji:{job_id}:auto")],
    ]
    if with_original:
        rows.append([InlineKeyboardButton(text="Оставить исходный эмодзи", callback_data=f"emoji:{job_id}:original")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def packs_keyboard(packs: list[Pack]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for pack in packs:
        marker = "✅ " if pack.is_active else ""
        role = "owner" if pack.role.value == "owner" else "editor"
        rows.append([
            InlineKeyboardButton(
                text=f"{marker}{pack.id}: {pack.title} ({role})",
                callback_data=f"pack:{pack.id}:activate",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
