import pytest

from app.bot.handlers_media import _add_job_with_emoji, _emoji_choice_text
from app.bot.keyboards import emoji_keyboard
from app.db.models import CropMode, JobStatus, MediaKind
from app.db.repo import Database
from app.services.pack_service import PackService


class _FakeTelegramStickerApi:
    async def create_set(self, **kwargs):
        return kwargs["short_name"]

    async def add_sticker(self, **kwargs):
        return None


class _FakeMessage:
    def __init__(self) -> None:
        self.stickers: list[object] = []

    async def answer_sticker(self, *, sticker):
        self.stickers.append(sticker)


def test_emoji_keyboard_keeps_crop_choice_on_emoji_step():
    keyboard = emoji_keyboard(42, ["😀", "🔥", "✨"], crop_mode=CropMode.FIT)

    crop_button = keyboard.inline_keyboard[-1][0]
    assert crop_button.text == "Обрезать до квадрата"
    assert crop_button.callback_data == "crop:42:square"


def test_emoji_choice_text_mentions_plain_emoji_message():
    text = _emoji_choice_text(title="Готово.", top=["😀", "🔥", "✨"], auto_pick="😀", from_gemma=True)

    assert "Авто (Gemma 4): 😀" in text
    assert "Можно просто отправить нужный эмодзи сообщением." in text


@pytest.mark.asyncio
async def test_add_job_with_emoji_sends_processed_sticker_preview_before_cleanup(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    tg_user_id = 5001
    user_id = await db.ensure_user(tg_user_id, "preview_user")
    await db.create_draft_pack(user_id, "Preview Pack", "preview_pack_by_mybot")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    processed_path = job_dir / "sticker.webp"
    preview_path = job_dir / "preview.png"
    processed_path.write_bytes(b"fake sticker")
    preview_path.write_bytes(b"fake preview")

    job = await db.create_media_job(
        user_id=user_id,
        telegram_file_id="file_1",
        telegram_file_unique_id="unique_1",
        media_kind=MediaKind.IMAGE,
        mime="image/png",
        original_name="source.png",
        temp_path=str(tmp_path / "source.png"),
    )
    await db.update_media_job_processing(
        job_id=job.id,
        crop_mode=CropMode.FIT,
        processed_path=str(processed_path),
        preview_path=str(preview_path),
        suggestions=["😀", "🔥", "✨"],
    )
    job = await db.get_media_job(job.id, user_id)
    assert job is not None

    message = _FakeMessage()
    pack_service = PackService(db=db, tg_api=_FakeTelegramStickerApi(), bot_username="mybot")

    text = await _add_job_with_emoji(
        db=db,
        pack_service=pack_service,
        reply_message=message,
        tg_user_id=tg_user_id,
        username="preview_user",
        job=job,
        emoji="😀",
    )

    assert len(message.stickers) == 1
    assert "Превью не удалось" not in text
    assert not job_dir.exists()

    saved = await db.get_media_job(job.id, user_id)
    assert saved is not None
    assert saved.status == JobStatus.DONE

    await db.close()
