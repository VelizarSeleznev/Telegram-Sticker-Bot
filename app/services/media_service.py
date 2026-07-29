from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

from app.db.models import CropMode, MediaKind

register_heif_opener()

STATIC_STICKER_LIMIT_BYTES = 512 * 1024
VIDEO_STICKER_LIMIT_BYTES = 256 * 1024
VIDEO_STICKER_TARGET_BYTES = 250 * 1024
VIDEO_STICKER_MAX_DURATION_SECONDS = 3.0
VIDEO_STICKER_MAX_FPS = 30.0

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessedSticker:
    path: Path
    preview_path: Path
    media_kind: MediaKind


@dataclass(slots=True)
class VideoProbe:
    codec_name: str
    width: int
    height: int
    duration: float
    fps: float
    audio_streams: int


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

    def process_video(
        self,
        input_path: Path,
        job_dir: Path,
        mode: CropMode,
        max_duration_seconds: float = VIDEO_STICKER_MAX_DURATION_SECONDS,
    ) -> ProcessedSticker:
        self._require_ffmpeg()
        if max_duration_seconds not in {3.0, 6.0}:
            raise ValueError("Video duration must be 3 or 6 seconds")
        job_dir.mkdir(parents=True, exist_ok=True)
        output_path = job_dir / "sticker.webm"
        preview_path = job_dir / "preview.png"
        source_probe = self._probe_video(input_path)
        duration = min(source_probe.duration, max_duration_seconds)
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео.")

        source_fps = source_probe.fps if source_probe.fps > 0 else VIDEO_STICKER_MAX_FPS
        output_fps = min(source_fps, VIDEO_STICKER_MAX_FPS)
        video_filter = self._video_filter(mode=mode, size=512, fps=output_fps)
        target_bitrate = max(
            30_000,
            int(VIDEO_STICKER_TARGET_BYTES * 8 / duration * 0.92),
        )
        best_path = job_dir / "best.webm"
        best_size = -1

        with tempfile.TemporaryDirectory(prefix="vp9-pass-", dir=job_dir) as pass_dir:
            for attempt in range(1, 5):
                passlog_path = Path(pass_dir) / f"attempt-{attempt}"
                self._encode_vp9_two_pass(
                    input_path=input_path,
                    output_path=output_path,
                    duration=duration,
                    video_filter=video_filter,
                    bitrate=target_bitrate,
                    passlog_path=passlog_path,
                )
                output_size = output_path.stat().st_size
                logger.info(
                    "VP9 encode attempt=%s duration=%.3f fps=%.3f bitrate=%s output_bytes=%s",
                    attempt,
                    duration,
                    output_fps,
                    target_bitrate,
                    output_size,
                )

                if output_size <= VIDEO_STICKER_LIMIT_BYTES and output_size > best_size:
                    shutil.copy2(output_path, best_path)
                    best_size = output_size

                if (
                    VIDEO_STICKER_TARGET_BYTES * 0.6
                    <= output_size
                    <= VIDEO_STICKER_LIMIT_BYTES
                ):
                    break

                if output_size > VIDEO_STICKER_LIMIT_BYTES:
                    target_bitrate = max(
                        20_000,
                        int(target_bitrate * VIDEO_STICKER_TARGET_BYTES / output_size * 0.90),
                    )
                elif attempt == 1:
                    target_bitrate = min(
                        6_000_000,
                        int(target_bitrate * VIDEO_STICKER_TARGET_BYTES / max(output_size, 1) * 0.95),
                    )
                else:
                    break

        if best_size < 0:
            raise RuntimeError("Не удалось ужать видео до лимита Telegram 256 КБ.")

        shutil.copy2(best_path, output_path)
        best_path.unlink(missing_ok=True)
        output_probe = self._probe_video(output_path)
        validation_error = self._video_sticker_validation_error(
            probe=output_probe,
            file_size=output_path.stat().st_size,
            max_duration_seconds=max_duration_seconds,
        )
        if validation_error:
            raise RuntimeError(f"Видео не прошло проверку Telegram: {validation_error}")

        if max_duration_seconds > VIDEO_STICKER_MAX_DURATION_SECONDS:
            self._patch_webm_duration(output_path, reported_duration=1.0)
            patched_probe = self._probe_video(output_path)
            validation_error = self._video_sticker_validation_error(
                probe=patched_probe,
                file_size=output_path.stat().st_size,
            )
            if validation_error:
                raise RuntimeError(f"Experimental WebM не прошел проверку: {validation_error}")

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
                result = self._to_sticker_canvas(image=image, mode=CropMode.FIT)
                self._save_webp_with_limit(result, output_path)
                result.save(preview_path, format="PNG", optimize=True)
            return ProcessedSticker(path=output_path, preview_path=preview_path, media_kind=MediaKind.IMAGE)

        self._require_ffmpeg()
        output_path = job_dir / "sticker.webm"
        preview_path = job_dir / "preview.png"

        if input_path.suffix.lower() == ".webm" and input_path.stat().st_size <= VIDEO_STICKER_LIMIT_BYTES:
            probe = self._probe_video(input_path)
            validation_error = self._video_sticker_validation_error(
                probe=probe,
                file_size=input_path.stat().st_size,
            )
            if validation_error is None:
                shutil.copy2(input_path, output_path)
            else:
                return self.process_video(input_path=input_path, job_dir=job_dir, mode=CropMode.FIT)
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
        if shutil.which("ffprobe") is None:
            raise RuntimeError("ffprobe не найден в системе")

    @staticmethod
    def _to_sticker_canvas(image: Image.Image, mode: CropMode) -> Image.Image:
        if mode == CropMode.SQUARE:
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            return image.resize((512, 512), Image.Resampling.LANCZOS)

        width, height = image.size
        if width >= height:
            target_width = 512
            target_height = max(1, round(height * 512 / width))
        else:
            target_height = 512
            target_width = max(1, round(width * 512 / height))
        if image.size != (target_width, target_height):
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
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
    def _video_filter(mode: CropMode, size: int, fps: float) -> str:
        fps_value = f"{fps:.3f}".rstrip("0").rstrip(".")
        if mode == CropMode.SQUARE:
            return (
                f"crop='min(iw,ih)':'min(iw,ih)',"
                f"scale={size}:{size}:flags=lanczos,"
                f"setsar=1,fps={fps_value}"
            )

        return (
            f"scale={size}:{size}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
            f"setsar=1,fps={fps_value}"
        )

    def _encode_vp9_two_pass(
        self,
        *,
        input_path: Path,
        output_path: Path,
        duration: float,
        video_filter: str,
        bitrate: int,
        passlog_path: Path,
    ) -> None:
        common = [
            "-i",
            str(input_path),
            "-t",
            f"{duration:.3f}",
            "-an",
            "-sn",
            "-map",
            "0:v:0",
            "-map_metadata",
            "-1",
            "-vf",
            video_filter,
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-b:v",
            str(bitrate),
            "-minrate",
            str(max(10_000, bitrate // 2)),
            "-maxrate",
            str(bitrate * 3 // 2),
            "-bufsize",
            str(bitrate * 2),
            "-deadline",
            "good",
            "-cpu-used",
            "4",
            "-row-mt",
            "1",
            "-auto-alt-ref",
            "0",
            "-passlogfile",
            str(passlog_path),
        ]
        self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                *common,
                "-pass",
                "1",
                "-f",
                "null",
                os.devnull,
            ]
        )
        self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                *common,
                "-pass",
                "2",
                str(output_path),
            ]
        )

    @staticmethod
    def _video_sticker_validation_error(
        *,
        probe: VideoProbe,
        file_size: int,
        max_duration_seconds: float = VIDEO_STICKER_MAX_DURATION_SECONDS,
    ) -> str | None:
        if probe.codec_name != "vp9":
            return "нужен кодек VP9"
        if max(probe.width, probe.height) != 512 or min(probe.width, probe.height) > 512:
            return "одна сторона должна быть ровно 512 px, вторая — не больше 512 px"
        if probe.duration <= 0 or probe.duration > max_duration_seconds:
            return f"длительность должна быть от 0 до {max_duration_seconds:g} секунд"
        if probe.fps <= 0 or probe.fps > VIDEO_STICKER_MAX_FPS:
            return "частота кадров должна быть не больше 30 FPS"
        if probe.audio_streams:
            return "аудиодорожка не допускается"
        if file_size > VIDEO_STICKER_LIMIT_BYTES:
            return "размер файла должен быть не больше 256 КБ"
        return None

    @staticmethod
    def _patch_webm_duration(path: Path, reported_duration: float) -> None:
        data = bytearray(path.read_bytes())
        marker = b"\x44\x89"
        cursor = 0

        while True:
            element_index = data.find(marker, cursor)
            if element_index < 0:
                raise RuntimeError("Не найдено поле Duration в WebM.")

            size_index = element_index + len(marker)
            if size_index >= len(data):
                raise RuntimeError("Повреждено поле Duration в WebM.")

            size_marker = data[size_index]
            if size_marker == 0x84:
                payload_index = size_index + 1
                payload = struct.pack(">f", reported_duration)
            elif size_marker == 0x88:
                payload_index = size_index + 1
                payload = struct.pack(">d", reported_duration)
            else:
                cursor = element_index + len(marker)
                continue

            payload_end = payload_index + len(payload)
            if payload_end > len(data):
                raise RuntimeError("Повреждено значение Duration в WebM.")

            data[payload_index:payload_end] = payload
            path.write_bytes(data)
            return

    @staticmethod
    def _probe_video(path: Path) -> VideoProbe:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffprobe failed")

        try:
            payload = json.loads(proc.stdout)
            streams = payload.get("streams") or []
            video = next(stream for stream in streams if stream.get("codec_type") == "video")
            duration_value = video.get("duration") or (payload.get("format") or {}).get("duration")
            frame_rate = str(video.get("avg_frame_rate") or "0/1")
            fps = float(Fraction(frame_rate)) if frame_rate != "0/0" else 0.0
            return VideoProbe(
                codec_name=str(video.get("codec_name") or ""),
                width=int(video.get("width") or 0),
                height=int(video.get("height") or 0),
                duration=float(duration_value or 0),
                fps=fps,
                audio_streams=sum(1 for stream in streams if stream.get("codec_type") == "audio"),
            )
        except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
            raise RuntimeError("Не удалось прочитать параметры видео через ffprobe.") from exc

    @staticmethod
    def _run_ffmpeg(cmd: list[str]) -> None:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg conversion failed")
