from PIL import Image

from app.db.models import CropMode, MediaKind
from app.services.media_service import MediaService


def test_process_image_square(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (800, 400), (255, 0, 0))
    image.save(source)

    svc = MediaService(temp_dir=tmp_path)
    out = svc.process_image(source, tmp_path / "job", CropMode.SQUARE)

    assert out.path.exists()
    assert out.preview_path.exists()
    with Image.open(out.preview_path) as preview:
        assert preview.size == (512, 512)


def test_process_existing_static_sticker_normalizes_small_canvas(tmp_path):
    source = tmp_path / "sticker.webp"
    image = Image.new("RGBA", (240, 240), (255, 0, 0, 255))
    image.save(source, format="WEBP")

    svc = MediaService(temp_dir=tmp_path)
    out = svc.process_existing_sticker(source, tmp_path / "job", MediaKind.IMAGE)

    with Image.open(out.path) as sticker:
        assert sticker.size == (512, 512)
    with Image.open(out.preview_path) as preview:
        assert preview.size == (512, 512)
