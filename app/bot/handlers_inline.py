from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import InlineQuery

from app.services.klipy_service import KlipyService

router = Router(name="inline")
logger = logging.getLogger(__name__)


@router.inline_query()
async def inline_klipy_search(query: InlineQuery, klipy_service: KlipyService) -> None:
    try:
        result = await klipy_service.search_inline_gifs(query.query, offset=query.offset)
        await query.answer(
            result.results,
            cache_time=60,
            is_personal=True,
            next_offset=result.next_offset,
        )
    except Exception:
        logger.exception("Klipy inline query failed")
        await query.answer([], cache_time=1, is_personal=True)
