from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.db.models import CropMode, JobStatus, MediaJob, MediaKind, Pack, PackStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def initialize(self) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name.startswith("."):
                continue
            script = path.read_text(encoding="utf-8")
            await self.conn.executescript(script)
        await self.conn.commit()

    async def ensure_user(self, tg_user_id: int) -> int:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "INSERT OR IGNORE INTO users(tg_user_id) VALUES (?)",
            (tg_user_id,),
        )
        row = await self._fetchone(
            "SELECT id FROM users WHERE tg_user_id = ?",
            (tg_user_id,),
        )
        await self.conn.commit()
        if row is None:
            raise RuntimeError("Failed to ensure user")
        return int(row["id"])

    async def create_draft_pack(self, user_id: int, title: str, short_name: str) -> Pack:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute("UPDATE packs SET is_active = 0, updated_at = ? WHERE user_id = ?", (now, user_id))
        cur = await self.conn.execute(
            """
            INSERT INTO packs(user_id, title, short_name, status, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (user_id, title, short_name, PackStatus.DRAFT.value, now, now),
        )
        await self.conn.commit()
        pack_id = cur.lastrowid
        if pack_id is None:
            raise RuntimeError("Failed to create pack")
        pack = await self.get_pack_by_id(pack_id)
        if not pack:
            raise RuntimeError("Pack read failed after insert")
        return pack

    async def list_packs(self, user_id: int) -> list[Pack]:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        rows = await self._fetchall(
            "SELECT * FROM packs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [self._row_to_pack(r) for r in rows]

    async def get_pack_by_id(self, pack_id: int) -> Pack | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone("SELECT * FROM packs WHERE id = ?", (pack_id,))
        return self._row_to_pack(row) if row else None

    async def get_active_pack(self, user_id: int) -> Pack | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT * FROM packs WHERE user_id = ? AND is_active = 1 LIMIT 1",
            (user_id,),
        )
        return self._row_to_pack(row) if row else None

    async def activate_pack(self, user_id: int, pack_id: int) -> bool:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        pack_row = await self._fetchone(
            "SELECT id FROM packs WHERE id = ? AND user_id = ?",
            (pack_id, user_id),
        )
        if not pack_row:
            return False
        now = _now()
        await self.conn.execute("UPDATE packs SET is_active = 0, updated_at = ? WHERE user_id = ?", (now, user_id))
        await self.conn.execute(
            "UPDATE packs SET is_active = 1, updated_at = ? WHERE id = ?",
            (now, pack_id),
        )
        await self.conn.commit()
        return True

    async def set_pack_ready(self, pack_id: int, tg_set_name: str) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute(
            "UPDATE packs SET status = ?, tg_set_name = ?, updated_at = ? WHERE id = ?",
            (PackStatus.READY.value, tg_set_name, now, pack_id),
        )
        await self.conn.commit()

    async def update_pack_short_name(self, pack_id: int, short_name: str) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute(
            "UPDATE packs SET short_name = ?, updated_at = ? WHERE id = ?",
            (short_name, now, pack_id),
        )
        await self.conn.commit()

    async def create_media_job(
        self,
        user_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        media_kind: MediaKind,
        mime: str | None,
        original_name: str | None,
        temp_path: str,
    ) -> MediaJob:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        cur = await self.conn.execute(
            """
            INSERT INTO media_jobs(
              user_id, telegram_file_id, telegram_file_unique_id, media_kind,
              mime, original_name, temp_path, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                telegram_file_id,
                telegram_file_unique_id,
                media_kind.value,
                mime,
                original_name,
                temp_path,
                JobStatus.PENDING.value,
                now,
                now,
            ),
        )
        await self.conn.commit()
        job_id = cur.lastrowid
        if job_id is None:
            raise RuntimeError("Failed to create media job")
        job = await self.get_media_job(job_id, user_id)
        if not job:
            raise RuntimeError("Media job read failed after insert")
        return job

    async def get_media_job(self, job_id: int, user_id: int) -> MediaJob | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT * FROM media_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        )
        return self._row_to_job(row) if row else None

    async def update_media_job_processing(
        self,
        job_id: int,
        crop_mode: CropMode,
        processed_path: str,
        preview_path: str,
        suggestions: list[str],
    ) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute(
            """
            UPDATE media_jobs
            SET crop_mode = ?, processed_path = ?, preview_path = ?, suggestions_json = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                crop_mode.value,
                processed_path,
                preview_path,
                json.dumps(suggestions, ensure_ascii=False),
                JobStatus.CROP_CHOSEN.value,
                now,
                job_id,
            ),
        )
        await self.conn.commit()

    async def set_media_job_status(self, job_id: int, status: JobStatus, error_text: str | None = None) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute(
            "UPDATE media_jobs SET status = ?, error_text = ?, updated_at = ? WHERE id = ?",
            (status.value, error_text, now, job_id),
        )
        await self.conn.commit()

    async def add_sticker_record(
        self,
        pack_id: int,
        media_kind: MediaKind,
        emoji: str,
        telegram_file_id: str | None,
        source_hash: str,
    ) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "INSERT INTO stickers(pack_id, media_kind, emoji, telegram_file_id, source_hash) VALUES (?, ?, ?, ?, ?)",
            (pack_id, media_kind.value, emoji, telegram_file_id, source_hash),
        )
        await self.conn.commit()

    def _row_to_pack(self, row: aiosqlite.Row | None) -> Pack | None:
        if row is None:
            return None
        return Pack(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            title=str(row["title"]),
            short_name=str(row["short_name"]),
            tg_set_name=row["tg_set_name"],
            status=PackStatus(str(row["status"])),
            is_active=bool(row["is_active"]),
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
        )

    def _row_to_job(self, row: aiosqlite.Row | None) -> MediaJob | None:
        if row is None:
            return None
        crop_mode_value = row["crop_mode"]
        return MediaJob(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            telegram_file_id=str(row["telegram_file_id"]),
            telegram_file_unique_id=str(row["telegram_file_unique_id"]),
            media_kind=MediaKind(str(row["media_kind"])),
            mime=row["mime"],
            original_name=row["original_name"],
            temp_path=str(row["temp_path"]),
            crop_mode=CropMode(crop_mode_value) if crop_mode_value else None,
            processed_path=row["processed_path"],
            preview_path=row["preview_path"],
            suggestions_json=row["suggestions_json"],
            status=JobStatus(str(row["status"])),
            error_text=row["error_text"],
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
        )

    async def _fetchone(self, query: str, params: tuple | list) -> aiosqlite.Row | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, query: str, params: tuple | list) -> list[aiosqlite.Row]:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()
