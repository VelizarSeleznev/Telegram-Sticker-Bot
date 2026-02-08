from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile, InputSticker

from app.db.models import MediaKind


class TelegramStickerApi:
    def __init__(self, bot: Bot, max_retries: int = 3) -> None:
        self.bot = bot
        self.max_retries = max_retries

    async def create_set(
        self,
        *,
        tg_user_id: int,
        title: str,
        short_name: str,
        media_kind: MediaKind,
        sticker_path: Path,
        emoji: str,
    ) -> str:
        sticker = InputSticker(
            sticker=FSInputFile(sticker_path),
            format="video" if media_kind == MediaKind.VIDEO else "static",
            emoji_list=[emoji],
        )

        async def _call() -> None:
            await self.bot.create_new_sticker_set(
                user_id=tg_user_id,
                name=short_name,
                title=title,
                stickers=[sticker],
                sticker_type="regular",
            )

        await self._retry(_call)
        return short_name

    async def add_sticker(
        self,
        *,
        tg_user_id: int,
        tg_set_name: str,
        media_kind: MediaKind,
        sticker_path: Path,
        emoji: str,
    ) -> None:
        sticker = InputSticker(
            sticker=FSInputFile(sticker_path),
            format="video" if media_kind == MediaKind.VIDEO else "static",
            emoji_list=[emoji],
        )

        async def _call() -> None:
            await self.bot.add_sticker_to_set(
                user_id=tg_user_id,
                name=tg_set_name,
                sticker=sticker,
            )

        await self._retry(_call)

    async def delete_sticker(self, *, sticker_file_id: str) -> None:
        async def _call() -> None:
            await self.bot.delete_sticker_from_set(sticker=sticker_file_id)

        await self._retry(_call)

    async def _retry(self, func) -> None:
        for attempt in range(1, self.max_retries + 1):
            try:
                await func()
                return
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except TelegramNetworkError:
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(attempt)
            except TelegramBadRequest:
                raise
