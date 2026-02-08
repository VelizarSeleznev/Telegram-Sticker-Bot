import pytest

from app.db.models import CropMode, JobStatus, MediaKind
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
