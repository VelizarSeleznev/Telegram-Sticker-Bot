import pytest

from app.db.models import MediaKind
from app.db.repo import Database
from app.services.collab_service import CollabService
from app.services.pack_service import PackService, generate_short_name


def test_generate_short_name_suffix_and_len() -> None:
    name = generate_short_name(
        title="Мой супер длинный стикерпак с символами !!!",
        tg_user_id=123,
        bot_username="mycoolbot",
        salt="x",
    )
    assert name.endswith("_by_mycoolbot")
    assert len(name) <= 64
    assert all(ch.isalnum() or ch == "_" for ch in name)


class _FakeTelegramStickerApi:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.add_calls: list[dict] = []

    async def create_set(self, **kwargs):
        self.create_calls.append(kwargs)
        return kwargs["short_name"]

    async def add_sticker(self, **kwargs):
        self.add_calls.append(kwargs)


@pytest.mark.asyncio
async def test_shared_pack_routes_create_and_add_via_owner_tg_id(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    owner_tg = 4001
    editor_tg = 4002
    owner_id = await db.ensure_user(owner_tg, "owner_four")
    await db.ensure_user(editor_tg, "editor_four")
    pack = await db.create_draft_pack(owner_id, "Shared Pack", "shared_pack_4_by_mybot")

    collab = CollabService(db=db, bot_username="mybot")
    _, invitation, _, _ = await collab.create_invitation_for_active_pack(
        requester_tg_user_id=owner_tg,
        requester_username="owner_four",
        invited_username_raw="@editor_four",
    )
    await collab.accept_invitation_by_token(
        token=invitation.token,
        accepter_tg_user_id=editor_tg,
        accepter_username="editor_four",
    )

    editor_id = await db.ensure_user(editor_tg, "editor_four")
    await db.set_active_pack_for_user(editor_id, pack.id)

    fake_api = _FakeTelegramStickerApi()
    svc = PackService(db=db, tg_api=fake_api, bot_username="mybot")

    sticker_path = tmp_path / "sticker.webp"
    sticker_path.write_bytes(b"fake")

    created_pack = await svc.add_processed_sticker(
        tg_user_id=editor_tg,
        media_kind=MediaKind.IMAGE,
        sticker_path=sticker_path,
        emoji="😀",
        username="editor_four",
    )
    assert created_pack.tg_set_name is not None
    assert fake_api.create_calls
    assert fake_api.create_calls[0]["tg_user_id"] == owner_tg

    await svc.add_processed_sticker(
        tg_user_id=editor_tg,
        media_kind=MediaKind.IMAGE,
        sticker_path=sticker_path,
        emoji="🔥",
        username="editor_four",
    )
    assert fake_api.add_calls
    assert fake_api.add_calls[0]["tg_user_id"] == owner_tg

    await db.close()
