from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from app.bot.handlers_inline import router as inline_router
from app.bot.handlers_media import router as media_router
from app.bot.handlers_menu import router as menu_router
from app.bot.handlers_packs import router as packs_router
from app.bot.handlers_start import router as start_router
from app.config import Settings
from app.db.repo import Database
from app.services.collab_service import CollabService
from app.services.emoji_service import EmojiService
from app.services.klipy_service import KlipyService
from app.services.media_service import MediaService
from app.services.pack_service import PackService
from app.services.telegram_sticker_api import TelegramStickerApi


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _get_me_with_retry(bot: Bot, attempts: int = 5) -> User:
    delay = 2
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await bot.get_me()
        except TelegramNetworkError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logging.getLogger(__name__).warning(
                "Telegram API unavailable during startup, retrying in %s seconds (%s/%s): %s",
                delay,
                attempt,
                attempts,
                exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    assert last_error is not None
    raise last_error


async def run() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)

    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    await db.connect()
    await db.initialize()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    me = await _get_me_with_retry(bot)
    if not me.username:
        raise RuntimeError("Bot username is required for sticker set naming")

    tg_api = TelegramStickerApi(bot)
    pack_service = PackService(db=db, tg_api=tg_api, bot_username=me.username)
    collab_service = CollabService(db=db, bot_username=me.username)
    media_service = MediaService(temp_dir=settings.temp_dir)
    emoji_service = EmojiService(catalog_path=settings.emoji_catalog_path)
    klipy_service = KlipyService(
        api_key=settings.klipy_api_key,
        client_key=settings.klipy_client_key,
        locale=settings.klipy_locale,
        country=settings.klipy_country,
        content_filter=settings.klipy_content_filter,
    )
    await asyncio.to_thread(emoji_service.initialize)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(inline_router)
    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(packs_router)
    dp.include_router(media_router)

    media_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)

    try:
        await dp.start_polling(
            bot,
            polling_timeout=settings.polling_timeout,
            settings=settings,
            db=db,
            tg_api=tg_api,
            pack_service=pack_service,
            collab_service=collab_service,
            media_service=media_service,
            emoji_service=emoji_service,
            klipy_service=klipy_service,
            media_semaphore=media_semaphore,
        )
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
