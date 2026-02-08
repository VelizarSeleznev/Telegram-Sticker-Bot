from app.db.models import MediaKind
from app.services.emoji_service import EmojiService


def test_emoji_service_fallback_when_model_unavailable(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '[{"emoji":"😀","descriptions":["smile"]},{"emoji":"🔥","descriptions":["fire"]}]',
        encoding="utf-8",
    )
    image = tmp_path / "preview.png"
    image.write_bytes(b"not-an-image")

    svc = EmojiService(catalog_path=catalog)
    result = svc.suggest(preview_path=image, media_kind=MediaKind.IMAGE)

    assert len(result.top3) == 3
    assert result.auto_pick == "🖼️"
