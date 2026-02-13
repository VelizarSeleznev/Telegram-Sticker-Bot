from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import CropMode, JobStatus, MediaKind, MemberRole
from app.db.repo import Database


@pytest.mark.asyncio
async def test_active_pack_switch(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    user_id = await db.ensure_user(100)
    first = await db.create_draft_pack(user_id, "Pack 1", "pack_1_by_bot")
    second = await db.create_draft_pack(user_id, "Pack 2", "pack_2_by_bot")

    active = await db.get_active_pack(user_id)
    assert active is not None
    assert active.id == second.id

    assert await db.activate_pack(user_id, first.id)
    active = await db.get_active_pack(user_id)
    assert active is not None
    assert active.id == first.id

    await db.close()


@pytest.mark.asyncio
async def test_media_job_transitions(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    user_id = await db.ensure_user(200)
    job = await db.create_media_job(
        user_id=user_id,
        telegram_file_id="f1",
        telegram_file_unique_id="u1",
        media_kind=MediaKind.IMAGE,
        mime="image/png",
        original_name="x.png",
        original_emoji="🙂",
        temp_path="/tmp/x.png",
    )
    assert job.status == JobStatus.PENDING
    assert job.original_emoji == "🙂"

    await db.update_media_job_processing(
        job_id=job.id,
        crop_mode=CropMode.SQUARE,
        processed_path="/tmp/processed.webp",
        preview_path="/tmp/preview.png",
        suggestions=["😀", "🔥", "✨"],
    )
    saved = await db.get_media_job(job.id, user_id)
    assert saved is not None
    assert saved.status == JobStatus.CROP_CHOSEN
    assert saved.crop_mode == CropMode.SQUARE

    await db.set_media_job_status(job.id, JobStatus.DONE)
    saved = await db.get_media_job(job.id, user_id)
    assert saved is not None
    assert saved.status == JobStatus.DONE

    await db.close()


@pytest.mark.asyncio
async def test_shared_pack_activation_and_roles(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    owner_id = await db.ensure_user(300)
    editor_id = await db.ensure_user(301, "editor_user")
    pack = await db.create_draft_pack(owner_id, "Shared", "shared_by_bot")

    invitation = await db.create_invitation(
        pack_id=pack.id,
        inviter_user_id=owner_id,
        invited_username_lc="editor_user",
        invited_user_id=editor_id,
        token="token_1",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
    )
    await db.accept_invitation(invitation.id, editor_id)

    assert await db.activate_pack(editor_id, pack.id)
    active = await db.get_active_pack(editor_id)
    assert active is not None
    assert active.id == pack.id
    assert active.role == MemberRole.EDITOR

    packs = await db.list_packs_for_user(editor_id)
    assert len(packs) == 1
    assert packs[0].id == pack.id
    assert packs[0].is_active is True
    assert packs[0].role == MemberRole.EDITOR

    await db.close()


@pytest.mark.asyncio
async def test_get_tg_user_id_by_user_id(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    user_id = await db.ensure_user(123456)
    assert await db.get_tg_user_id_by_user_id(user_id) == 123456
    assert await db.get_tg_user_id_by_user_id(999999) is None

    await db.close()
