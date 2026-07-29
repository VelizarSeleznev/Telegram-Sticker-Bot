import shutil
import subprocess

import pytest
from PIL import Image

from app.db.models import CropMode, MediaKind
from app.services.media_service import (
    VIDEO_STICKER_LIMIT_BYTES,
    MediaService,
    VideoProbe,
)


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


def test_video_sticker_validation_rejects_non_telegram_dimensions():
    probe = VideoProbe(
        codec_name="vp9",
        width=448,
        height=448,
        duration=3.0,
        fps=30.0,
        audio_streams=0,
    )

    error = MediaService._video_sticker_validation_error(
        probe=probe,
        file_size=VIDEO_STICKER_LIMIT_BYTES,
    )

    assert error is not None
    assert "512" in error


def test_video_sticker_validation_accepts_telegram_ready_output():
    probe = VideoProbe(
        codec_name="vp9",
        width=512,
        height=384,
        duration=3.0,
        fps=30.0,
        audio_streams=0,
    )

    assert (
        MediaService._video_sticker_validation_error(
            probe=probe,
            file_size=VIDEO_STICKER_LIMIT_BYTES,
        )
        is None
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)
def test_process_video_produces_telegram_ready_file(tmp_path):
    source = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=60",
            "-t",
            "6.2",
            "-c:v",
            "ffv1",
            str(source),
        ],
        check=True,
    )

    svc = MediaService(temp_dir=tmp_path)
    out = svc.process_video(source, tmp_path / "job", CropMode.FIT)
    probe = svc._probe_video(out.path)

    assert out.path.stat().st_size <= VIDEO_STICKER_LIMIT_BYTES
    assert probe.codec_name == "vp9"
    assert probe.width == 512
    assert probe.height == 512
    assert probe.duration <= 3.0
    assert probe.fps <= 30.0
    assert probe.audio_streams == 0

    experimental = svc.process_video(
        source,
        tmp_path / "job-experimental",
        CropMode.FIT,
        max_duration_seconds=6.0,
    )
    experimental_probe = svc._probe_video(experimental.path)
    packet_times = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            str(experimental.path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()

    assert 0 < experimental_probe.duration <= 1.0
    assert float(packet_times[-1].split(",", 1)[0]) > 5.9
