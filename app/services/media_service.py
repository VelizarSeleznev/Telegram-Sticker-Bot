from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

from app.db.models import CropMode, MediaKind

register_heif_opener()

STATIC_STICKER_LIMIT_BYTES = 512 * 1024
VIDEO_STICKER_LIMIT_BYTES = 256 * 1024


@dataclass(slots=True)
class ProcessedSticker:
    path: Path
    preview_path: Path
    media_kind: MediaKind


class MediaService:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def infer_media_kind(mime: str | None, filename: str | None) -> MediaKind | None:
        value = (mime or "").lower()
        name = (filename or "").lower()
        if value.startswith("image/") or any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic")):
            return MediaKind.IMAGE
        if value.startswith("video/") or any(name.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".gif", ".mkv")):
            return MediaKind.VIDEO
        return None

    def process_image(self, input_path: Path, job_dir: Path, mode: CropMode) -> ProcessedSticker:
        job_dir.mkdir(parents=True, exist_ok=True)
        output_path = job_dir / "sticker.webp"
        preview_path = job_dir / "preview.png"

        with Image.open(input_path) as source:
            image = source.convert("RGBA")
            result = self._to_sticker_canvas(image=image, mode=mode)
            self._save_webp_with_limit(result, output_path)
            result.save(preview_path, format="PNG", optimize=True)

        return ProcessedSticker(path=output_path, preview_path=preview_path, media_kind=MediaKind.IMAGE)

    def process_video(self, input_path: Path, job_dir: Path, mode: CropMode) -> ProcessedSticker:
        self._require_ffmpeg()
        job_dir.mkdir(parents=True, exist_ok=True)
        output_path = job_dir / "sticker.webm"
        preview_path = job_dir / "preview.png"

        profiles = [
            (512, 30, 34),
            (512, 24, 38),
            (448, 24, 40),
            (384, 20, 44),
        ]

        for size, fps, crf in profiles:
            vf = self._video_filter(mode=mode, size=size, fps=fps)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-t",
                "3",
                "-an",
                "-vf",
                vf,
                "-c:v",
                "libvpx-vp9",
                "-b:v",
                "0",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuva420p",
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                str(output_path),
            ]
            self._run_ffmpeg(cmd)
            if output_path.stat().st_size <= VIDEO_STICKER_LIMIT_BYTES:
                break
        else:
            raise RuntimeError("Видео слишком тяжелое даже после сжатия. Попробуйте более простое/короткое видео.")

        preview_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            str(preview_path),
        ]
        self._run_ffmpeg(preview_cmd)

        return ProcessedSticker(path=output_path, preview_path=preview_path, media_kind=MediaKind.VIDEO)

    def process_existing_sticker(self, input_path: Path, job_dir: Path, media_kind: MediaKind) -> ProcessedSticker:
        job_dir.mkdir(parents=True, exist_ok=True)
        if media_kind == MediaKind.IMAGE:
            output_path = job_dir / "sticker.webp"
            preview_path = job_dir / "preview.png"
            with Image.open(input_path) as source:
                image = source.convert("RGBA")
                self._save_webp_with_limit(image, output_path)
                image.save(preview_path, format="PNG", optimize=True)
            return ProcessedSticker(path=output_path, preview_path=preview_path, media_kind=MediaKind.IMAGE)

        self._require_ffmpeg()
        output_path = job_dir / "sticker.webm"
        preview_path = job_dir / "preview.png"

        if input_path.suffix.lower() == ".webm" and input_path.stat().st_size <= VIDEO_STICKER_LIMIT_BYTES:
            shutil.copy2(input_path, output_path)
        else:
            return self.process_video(input_path=input_path, job_dir=job_dir, mode=CropMode.FIT)

        preview_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            str(preview_path),
        ]
        self._run_ffmpeg(preview_cmd)
        return ProcessedSticker(path=output_path, preview_path=preview_path, media_kind=MediaKind.VIDEO)

    @staticmethod
    def cleanup_job_dir(job_dir: Path) -> None:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)

    @staticmethod
    def _require_ffmpeg() -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg не найден в системе")

    @staticmethod
    def _to_sticker_canvas(image: Image.Image, mode: CropMode) -> Image.Image:
        if mode == CropMode.SQUARE:
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            return image.resize((512, 512), Image.Resampling.LANCZOS)

        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        offset_x = (512 - image.width) // 2
        offset_y = (512 - image.height) // 2
        canvas.alpha_composite(image, (offset_x, offset_y))
        return canvas

    @staticmethod
    def _save_webp_with_limit(image: Image.Image, output_path: Path) -> None:
        for quality in [95, 90, 85, 80, 75, 70, 65, 60, 55, 50]:
            image.save(output_path, format="WEBP", quality=quality, method=6)
            if output_path.stat().st_size <= STATIC_STICKER_LIMIT_BYTES:
                return
        raise RuntimeError("Изображение слишком тяжелое после сжатия. Попробуйте другое изображение.")

    @staticmethod
    def _video_filter(mode: CropMode, size: int, fps: int) -> str:
        if mode == CropMode.SQUARE:
            return (
                f"crop='min(iw,ih)':'min(iw,ih)',"
                f"scale={size}:{size}:flags=lanczos,"
                f"fps={fps}"
            )

        return (
            f"scale='if(gt(iw,ih),{size},-2)':'if(gt(ih,iw),{size},-2)':flags=lanczos,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
            f"fps={fps}"
        )

    @staticmethod
    def _run_ffmpeg(cmd: list[str]) -> None:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg conversion failed")
