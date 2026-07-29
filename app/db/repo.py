from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.db.models import (
    CropMode,
    InvitationStatus,
    JobStatus,
    MediaJob,
    MediaKind,
    MemberRole,
    Pack,
    PackInvitation,
    PackMember,
    PackStatus,
    StickerAction,
    StickerActionKind,
)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _normalize_username_lc(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lstrip("@").lower()
    if not USERNAME_RE.fullmatch(candidate):
        return None
    return candidate


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
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              name TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name.startswith("."):
                continue
            existing = await self._fetchone("SELECT 1 FROM schema_migrations WHERE name = ?", (path.name,))
            if existing:
                continue
            script = path.read_text(encoding="utf-8")
            await self.conn.executescript(script)
            await self.conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                (path.name, _now()),
            )
        await self.conn.commit()

    async def ensure_user(self, tg_user_id: int, username_lc: str | None = None) -> int:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        normalized_username = _normalize_username_lc(username_lc)
        await self.conn.execute(
            "INSERT OR IGNORE INTO users(tg_user_id) VALUES (?)",
            (tg_user_id,),
        )
        if normalized_username:
            await self.conn.execute(
                "UPDATE users SET username_lc = NULL WHERE username_lc = ? AND tg_user_id != ?",
                (normalized_username, tg_user_id),
            )
            await self.conn.execute(
                "UPDATE users SET username_lc = ? WHERE tg_user_id = ?",
                (normalized_username, tg_user_id),
            )
        row = await self._fetchone(
            "SELECT id FROM users WHERE tg_user_id = ?",
            (tg_user_id,),
        )
        await self.conn.commit()
        if row is None:
            raise RuntimeError("Failed to ensure user")
        return int(row["id"])

    async def get_user_by_username(self, username_lc: str) -> aiosqlite.Row | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        return await self._fetchone(
            "SELECT id, tg_user_id, username_lc FROM users WHERE username_lc = ? LIMIT 1",
            (username_lc,),
        )

    async def get_tg_user_id_by_user_id(self, user_id: int) -> int | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone("SELECT tg_user_id FROM users WHERE id = ? LIMIT 1", (user_id,))
        return int(row["tg_user_id"]) if row else None

    async def get_video_duration_seconds(self, user_id: int) -> int:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT video_duration_seconds FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        )
        return int(row["video_duration_seconds"]) if row else 3

    async def set_video_duration_seconds(self, user_id: int, seconds: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        if seconds not in {3, 6}:
            raise ValueError("Video duration must be 3 or 6 seconds")
        await self.conn.execute(
            "UPDATE users SET video_duration_seconds = ? WHERE id = ?",
            (seconds, user_id),
        )
        await self.conn.commit()

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
        pack_id = cur.lastrowid
        if pack_id is None:
            raise RuntimeError("Failed to create pack")

        await self.conn.execute(
            """
            INSERT OR IGNORE INTO pack_members(pack_id, user_id, role, invited_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (pack_id, user_id, MemberRole.OWNER.value, now, now),
        )
        await self.conn.execute(
            """
            INSERT INTO user_active_packs(user_id, pack_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pack_id = excluded.pack_id, updated_at = excluded.updated_at
            """,
            (user_id, pack_id, now),
        )

        await self.conn.commit()
        pack = await self.get_pack_by_id(pack_id, requester_user_id=user_id)
        if not pack:
            raise RuntimeError("Pack read failed after insert")
        return pack

    async def list_packs(self, user_id: int) -> list[Pack]:
        return await self.list_packs_for_user(user_id)

    async def list_packs_for_user(self, user_id: int) -> list[Pack]:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        rows = await self._fetchall(
            """
            SELECT
              p.*,
              pm.role AS member_role,
              CASE WHEN uap.pack_id = p.id THEN 1 ELSE 0 END AS member_active
            FROM packs p
            INNER JOIN pack_members pm
              ON pm.pack_id = p.id AND pm.user_id = ?
            LEFT JOIN user_active_packs uap
              ON uap.user_id = ?
            ORDER BY member_active DESC, p.updated_at DESC, p.id DESC
            """,
            (user_id, user_id),
        )
        return [self._row_to_pack(r) for r in rows]

    async def get_pack_by_tg_set_name_for_user(self, tg_set_name: str, requester_user_id: int) -> Pack | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            """
            SELECT
              p.*,
              pm.role AS member_role,
              CASE WHEN uap.pack_id = p.id THEN 1 ELSE 0 END AS member_active
            FROM packs p
            INNER JOIN pack_members pm
              ON pm.pack_id = p.id AND pm.user_id = ?
            LEFT JOIN user_active_packs uap
              ON uap.user_id = ?
            WHERE p.tg_set_name = ?
            LIMIT 1
            """,
            (requester_user_id, requester_user_id, tg_set_name),
        )
        return self._row_to_pack(row) if row else None

    async def get_pack_by_id(self, pack_id: int, requester_user_id: int | None = None) -> Pack | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        if requester_user_id is None:
            row = await self._fetchone("SELECT * FROM packs WHERE id = ?", (pack_id,))
            return self._row_to_pack(row) if row else None

        row = await self._fetchone(
            """
            SELECT
              p.*,
              pm.role AS member_role,
              CASE WHEN uap.pack_id = p.id THEN 1 ELSE 0 END AS member_active
            FROM packs p
            INNER JOIN pack_members pm
              ON pm.pack_id = p.id AND pm.user_id = ?
            LEFT JOIN user_active_packs uap
              ON uap.user_id = ?
            WHERE p.id = ?
            LIMIT 1
            """,
            (requester_user_id, requester_user_id, pack_id),
        )
        return self._row_to_pack(row) if row else None

    async def get_active_pack(self, user_id: int) -> Pack | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")

        row = await self._fetchone(
            """
            SELECT
              p.*,
              pm.role AS member_role,
              1 AS member_active
            FROM user_active_packs uap
            INNER JOIN packs p
              ON p.id = uap.pack_id
            INNER JOIN pack_members pm
              ON pm.pack_id = p.id AND pm.user_id = ?
            WHERE uap.user_id = ?
            LIMIT 1
            """,
            (user_id, user_id),
        )
        if row:
            return self._row_to_pack(row)

        stale = await self._fetchone(
            """
            SELECT uap.user_id
            FROM user_active_packs uap
            LEFT JOIN pack_members pm
              ON pm.pack_id = uap.pack_id AND pm.user_id = uap.user_id
            WHERE uap.user_id = ? AND pm.user_id IS NULL
            LIMIT 1
            """,
            (user_id,),
        )
        if stale:
            await self.conn.execute("DELETE FROM user_active_packs WHERE user_id = ?", (user_id,))
            await self.conn.commit()
        return None

    async def activate_pack(self, user_id: int, pack_id: int) -> bool:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        role = await self.get_pack_role(pack_id=pack_id, user_id=user_id)
        if role is None:
            return False

        now = _now()
        await self.conn.execute(
            """
            INSERT INTO user_active_packs(user_id, pack_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pack_id = excluded.pack_id, updated_at = excluded.updated_at
            """,
            (user_id, pack_id, now),
        )

        if role == MemberRole.OWNER:
            await self.conn.execute("UPDATE packs SET is_active = 0, updated_at = ? WHERE user_id = ?", (now, user_id))
            await self.conn.execute("UPDATE packs SET is_active = 1, updated_at = ? WHERE id = ?", (now, pack_id))

        await self.conn.commit()
        return True

    async def set_active_pack_for_user(self, user_id: int, pack_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            """
            INSERT INTO user_active_packs(user_id, pack_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pack_id = excluded.pack_id, updated_at = excluded.updated_at
            """,
            (user_id, pack_id, _now()),
        )
        await self.conn.commit()

    async def clear_active_pack_for_user(self, user_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute("DELETE FROM user_active_packs WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def is_pack_member(self, pack_id: int, user_id: int) -> bool:
        return await self.get_pack_role(pack_id=pack_id, user_id=user_id) is not None

    async def get_pack_role(self, pack_id: int, user_id: int) -> MemberRole | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT role FROM pack_members WHERE pack_id = ? AND user_id = ? LIMIT 1",
            (pack_id, user_id),
        )
        if not row:
            return None
        return MemberRole(str(row["role"]))

    async def get_pack_owner_user_id(self, pack_id: int) -> int | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT user_id FROM pack_members WHERE pack_id = ? AND role = ? LIMIT 1",
            (pack_id, MemberRole.OWNER.value),
        )
        if row:
            return int(row["user_id"])

        legacy = await self._fetchone("SELECT user_id FROM packs WHERE id = ? LIMIT 1", (pack_id,))
        return int(legacy["user_id"]) if legacy else None

    async def get_pack_owner_tg_user_id(self, pack_id: int) -> int | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            """
            SELECT u.tg_user_id
            FROM pack_members pm
            INNER JOIN users u ON u.id = pm.user_id
            WHERE pm.pack_id = ? AND pm.role = ?
            LIMIT 1
            """,
            (pack_id, MemberRole.OWNER.value),
        )
        if row:
            return int(row["tg_user_id"])

        legacy = await self._fetchone(
            "SELECT u.tg_user_id FROM packs p INNER JOIN users u ON u.id = p.user_id WHERE p.id = ? LIMIT 1",
            (pack_id,),
        )
        return int(legacy["tg_user_id"]) if legacy else None

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
        original_emoji: str | None = None,
    ) -> MediaJob:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        cur = await self.conn.execute(
            """
            INSERT INTO media_jobs(
              user_id, telegram_file_id, telegram_file_unique_id, media_kind,
              mime, original_name, original_emoji, temp_path, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                telegram_file_id,
                telegram_file_unique_id,
                media_kind.value,
                mime,
                original_name,
                original_emoji,
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

    async def expire_sticker_actions(self) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "DELETE FROM sticker_actions WHERE expires_at <= ?",
            (_now(),),
        )
        await self.conn.commit()

    async def create_sticker_action(
        self,
        *,
        user_id: int,
        kind: StickerActionKind,
        sticker_file_id: str,
        sticker_file_unique_id: str,
        sticker_set_name: str | None,
        original_emoji: str | None,
        expires_at: str,
    ) -> str:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.expire_sticker_actions()
        token = secrets.token_urlsafe(24)
        await self.conn.execute(
            """
            INSERT INTO sticker_actions(
              user_id, action_token, kind, sticker_file_id, sticker_file_unique_id,
              sticker_set_name, original_emoji, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                token,
                kind.value,
                sticker_file_id,
                sticker_file_unique_id,
                sticker_set_name,
                original_emoji,
                expires_at,
            ),
        )
        await self.conn.commit()
        return token

    async def get_sticker_action(self, token: str, user_id: int) -> StickerAction | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT * FROM sticker_actions WHERE action_token = ? AND user_id = ? LIMIT 1",
            (token, user_id),
        )
        return self._row_to_sticker_action(row) if row else None

    async def delete_sticker_action(self, token: str, user_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "DELETE FROM sticker_actions WHERE action_token = ? AND user_id = ?",
            (token, user_id),
        )
        await self.conn.commit()

    async def delete_sticker_actions_for_unique_id(self, user_id: int, sticker_file_unique_id: str) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "DELETE FROM sticker_actions WHERE user_id = ? AND sticker_file_unique_id = ?",
            (user_id, sticker_file_unique_id),
        )
        await self.conn.commit()

    async def expire_pending_invitations(self) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        await self.conn.execute(
            """
            UPDATE pack_invitations
            SET status = ?, updated_at = ?
            WHERE status = ? AND expires_at <= ?
            """,
            (InvitationStatus.EXPIRED.value, now, InvitationStatus.PENDING.value, now),
        )
        await self.conn.commit()

    async def count_editors(self, pack_id: int) -> int:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            "SELECT COUNT(*) AS cnt FROM pack_members WHERE pack_id = ? AND role = ?",
            (pack_id, MemberRole.EDITOR.value),
        )
        return int(row["cnt"]) if row else 0

    async def find_pending_invitation(self, pack_id: int, invited_username_lc: str) -> PackInvitation | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            """
            SELECT pi.*, p.title AS pack_title
            FROM pack_invitations pi
            INNER JOIN packs p ON p.id = pi.pack_id
            WHERE pi.pack_id = ? AND pi.invited_username_lc = ? AND pi.status = ?
            ORDER BY pi.id DESC
            LIMIT 1
            """,
            (pack_id, invited_username_lc, InvitationStatus.PENDING.value),
        )
        return self._row_to_invitation(row) if row else None

    async def create_invitation(
        self,
        pack_id: int,
        inviter_user_id: int,
        invited_username_lc: str,
        invited_user_id: int | None,
        token: str,
        expires_at: str,
    ) -> PackInvitation:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        cur = await self.conn.execute(
            """
            INSERT INTO pack_invitations(
              pack_id, inviter_user_id, invited_username_lc, invited_user_id,
              token, status, expires_at, accepted_by_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                pack_id,
                inviter_user_id,
                invited_username_lc,
                invited_user_id,
                token,
                InvitationStatus.PENDING.value,
                expires_at,
                now,
                now,
            ),
        )
        await self.conn.commit()
        invitation_id = cur.lastrowid
        if invitation_id is None:
            raise RuntimeError("Failed to create invitation")
        row = await self._fetchone(
            """
            SELECT pi.*, p.title AS pack_title
            FROM pack_invitations pi
            INNER JOIN packs p ON p.id = pi.pack_id
            WHERE pi.id = ?
            """,
            (invitation_id,),
        )
        if not row:
            raise RuntimeError("Failed to read invitation after insert")
        return self._row_to_invitation(row)

    async def get_invitation_by_token(self, token: str) -> PackInvitation | None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        row = await self._fetchone(
            """
            SELECT pi.*, p.title AS pack_title
            FROM pack_invitations pi
            INNER JOIN packs p ON p.id = pi.pack_id
            WHERE pi.token = ?
            LIMIT 1
            """,
            (token,),
        )
        return self._row_to_invitation(row) if row else None

    async def list_pack_members(self, pack_id: int) -> list[PackMember]:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        rows = await self._fetchall(
            """
            SELECT
              pm.pack_id,
              pm.user_id,
              pm.role,
              pm.invited_by_user_id,
              pm.created_at,
              pm.updated_at,
              u.tg_user_id,
              u.username_lc
            FROM pack_members pm
            INNER JOIN users u ON u.id = pm.user_id
            WHERE pm.pack_id = ?
            ORDER BY CASE pm.role WHEN 'owner' THEN 0 ELSE 1 END, pm.created_at ASC
            """,
            (pack_id,),
        )
        return [self._row_to_member(r) for r in rows]

    async def list_pending_invitations(self, pack_id: int) -> list[PackInvitation]:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        rows = await self._fetchall(
            """
            SELECT pi.*, p.title AS pack_title
            FROM pack_invitations pi
            INNER JOIN packs p ON p.id = pi.pack_id
            WHERE pi.pack_id = ? AND pi.status = ?
            ORDER BY pi.created_at DESC
            """,
            (pack_id, InvitationStatus.PENDING.value),
        )
        return [self._row_to_invitation(r) for r in rows]

    async def accept_invitation(self, invitation_id: int, accepted_user_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        now = _now()
        invitation = await self._fetchone(
            "SELECT pack_id, inviter_user_id FROM pack_invitations WHERE id = ?",
            (invitation_id,),
        )
        if not invitation:
            raise RuntimeError("Invitation not found")

        pack_id = int(invitation["pack_id"])
        inviter_user_id = int(invitation["inviter_user_id"])

        await self.conn.execute(
            """
            UPDATE pack_invitations
            SET status = ?, accepted_by_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (InvitationStatus.ACCEPTED.value, accepted_user_id, now, invitation_id),
        )
        await self.conn.execute(
            """
            INSERT INTO pack_members(pack_id, user_id, role, invited_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(pack_id, user_id)
            DO UPDATE SET role = excluded.role, invited_by_user_id = excluded.invited_by_user_id, updated_at = excluded.updated_at
            """,
            (pack_id, accepted_user_id, MemberRole.EDITOR.value, inviter_user_id, now, now),
        )
        await self.conn.commit()

    async def revoke_invitation(self, invitation_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "UPDATE pack_invitations SET status = ?, updated_at = ? WHERE id = ?",
            (InvitationStatus.REVOKED.value, _now(), invitation_id),
        )
        await self.conn.commit()

    async def expire_invitation(self, invitation_id: int) -> None:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        await self.conn.execute(
            "UPDATE pack_invitations SET status = ?, updated_at = ? WHERE id = ?",
            (InvitationStatus.EXPIRED.value, _now(), invitation_id),
        )
        await self.conn.commit()

    async def remove_member(self, pack_id: int, user_id: int) -> bool:
        if not self.conn:
            raise RuntimeError("DB is not connected")
        cur = await self.conn.execute(
            "DELETE FROM pack_members WHERE pack_id = ? AND user_id = ?",
            (pack_id, user_id),
        )
        await self.conn.execute(
            "DELETE FROM user_active_packs WHERE user_id = ? AND pack_id = ?",
            (user_id, pack_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    def _row_to_pack(self, row: aiosqlite.Row | None) -> Pack | None:
        if row is None:
            return None
        keys = row.keys()
        role_value = row["member_role"] if "member_role" in keys and row["member_role"] else MemberRole.OWNER.value
        active_value = row["member_active"] if "member_active" in keys else row["is_active"]
        return Pack(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            title=str(row["title"]),
            short_name=str(row["short_name"]),
            tg_set_name=row["tg_set_name"],
            status=PackStatus(str(row["status"])),
            is_active=bool(active_value),
            role=MemberRole(str(role_value)),
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
        )

    def _row_to_job(self, row: aiosqlite.Row | None) -> MediaJob | None:
        if row is None:
            return None
        crop_mode_value = row["crop_mode"]
        row_keys = row.keys()
        return MediaJob(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            telegram_file_id=str(row["telegram_file_id"]),
            telegram_file_unique_id=str(row["telegram_file_unique_id"]),
            media_kind=MediaKind(str(row["media_kind"])),
            mime=row["mime"],
            original_name=row["original_name"],
            original_emoji=row["original_emoji"] if "original_emoji" in row_keys else None,
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

    def _row_to_member(self, row: aiosqlite.Row) -> PackMember:
        return PackMember(
            pack_id=int(row["pack_id"]),
            user_id=int(row["user_id"]),
            role=MemberRole(str(row["role"])),
            invited_by_user_id=int(row["invited_by_user_id"]) if row["invited_by_user_id"] is not None else None,
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
            tg_user_id=int(row["tg_user_id"]),
            username_lc=row["username_lc"],
        )

    def _row_to_invitation(self, row: aiosqlite.Row) -> PackInvitation:
        return PackInvitation(
            id=int(row["id"]),
            pack_id=int(row["pack_id"]),
            inviter_user_id=int(row["inviter_user_id"]),
            invited_username_lc=str(row["invited_username_lc"]),
            invited_user_id=int(row["invited_user_id"]) if row["invited_user_id"] is not None else None,
            token=str(row["token"]),
            status=InvitationStatus(str(row["status"])),
            expires_at=_parse_dt(str(row["expires_at"])),
            accepted_by_user_id=int(row["accepted_by_user_id"]) if row["accepted_by_user_id"] is not None else None,
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
            pack_title=row["pack_title"] if "pack_title" in row.keys() else None,
        )

    def _row_to_sticker_action(self, row: aiosqlite.Row) -> StickerAction:
        return StickerAction(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            action_token=str(row["action_token"]),
            kind=StickerActionKind(str(row["kind"])),
            sticker_file_id=str(row["sticker_file_id"]),
            sticker_file_unique_id=str(row["sticker_file_unique_id"]),
            sticker_set_name=row["sticker_set_name"],
            original_emoji=row["original_emoji"],
            created_at=_parse_dt(str(row["created_at"])),
            expires_at=_parse_dt(str(row["expires_at"])),
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
