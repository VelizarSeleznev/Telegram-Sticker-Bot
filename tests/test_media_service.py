from PIL import Image

from app.db.models import CropMode
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
