from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import StickerActionKind
from app.db.repo import Database


@pytest.mark.asyncio
async def test_sticker_actions_create_get_delete_and_expire(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    user_id = await db.ensure_user(999, "user_999")
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=10)).isoformat(timespec="seconds")

    token = await db.create_sticker_action(
        user_id=user_id,
        kind=StickerActionKind.DELETE,
        sticker_file_id="f1",
        sticker_file_unique_id="u1",
        sticker_set_name="set1",
        original_emoji="😀",
        expires_at=expires_at,
    )

    got = await db.get_sticker_action(token, user_id)
    assert got is not None
    assert got.kind == StickerActionKind.DELETE
    assert got.sticker_file_unique_id == "u1"

    await db.delete_sticker_actions_for_unique_id(user_id, "u1")
    got2 = await db.get_sticker_action(token, user_id)
    assert got2 is None

    token2 = await db.create_sticker_action(
        user_id=user_id,
        kind=StickerActionKind.IMPORT,
        sticker_file_id="f2",
        sticker_file_unique_id="u2",
        sticker_set_name=None,
        original_emoji=None,
        expires_at=(now - timedelta(seconds=1)).isoformat(timespec="seconds"),
    )
    assert token2
    await db.expire_sticker_actions()
    assert await db.get_sticker_action(token2, user_id) is None

    await db.close()
