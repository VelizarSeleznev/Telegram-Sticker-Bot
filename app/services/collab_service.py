from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from app.db.models import InvitationStatus, MemberRole, Pack, PackInvitation, PackMember
from app.db.repo import Database

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


class CollabService:
    def __init__(
        self,
        db: Database,
        bot_username: str,
        invite_ttl_hours: int = 24,
        max_editors_per_pack: int = 20,
    ) -> None:
        self.db = db
        self.bot_username = bot_username
        self.invite_ttl_hours = invite_ttl_hours
        self.max_editors_per_pack = max_editors_per_pack

    @staticmethod
    def normalize_username(username: str | None) -> str | None:
        if not username:
            return None
        normalized = username.strip().lstrip("@").lower()
        if not USERNAME_RE.fullmatch(normalized):
            return None
        return normalized

    async def touch_user(self, tg_user_id: int, username: str | None) -> int:
        return await self.db.ensure_user(tg_user_id, self.normalize_username(username))

    async def create_invitation_for_active_pack(
        self,
        *,
        requester_tg_user_id: int,
        requester_username: str | None,
        invited_username_raw: str,
    ) -> tuple[Pack, PackInvitation, str, int | None]:
        requester_id = await self.touch_user(requester_tg_user_id, requester_username)
        await self.db.expire_pending_invitations()

        active = await self.db.get_active_pack(requester_id)
        if not active:
            raise RuntimeError("Нет активного пака. Выберите через /packs или создайте /newpack")

        role = await self.db.get_pack_role(active.id, requester_id)
        if role not in {MemberRole.OWNER, MemberRole.EDITOR}:
            raise RuntimeError("У вас нет прав приглашать участников в этот пак")

        invited_username_lc = self.normalize_username(invited_username_raw)
        if not invited_username_lc:
            raise RuntimeError("Формат: /invite @username")

        requester_username_lc = self.normalize_username(requester_username)
        if requester_username_lc and invited_username_lc == requester_username_lc:
            raise RuntimeError("Нельзя пригласить самого себя")

        invited_user = await self.db.get_user_by_username(invited_username_lc)
        invited_user_id: int | None = int(invited_user["id"]) if invited_user else None
        invited_tg_user_id: int | None = int(invited_user["tg_user_id"]) if invited_user else None

        if invited_user_id is not None and await self.db.is_pack_member(active.id, invited_user_id):
            raise RuntimeError("Этот пользователь уже участник пака")

        editors_count = await self.db.count_editors(active.id)
        if editors_count >= self.max_editors_per_pack:
            raise RuntimeError(f"Лимит редакторов достигнут ({self.max_editors_per_pack})")

        existing_pending = await self.db.find_pending_invitation(active.id, invited_username_lc)
        if existing_pending:
            await self.db.revoke_invitation(existing_pending.id)

        token = secrets.token_urlsafe(24)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=self.invite_ttl_hours)).isoformat(timespec="seconds")
        invitation = await self.db.create_invitation(
            pack_id=active.id,
            inviter_user_id=requester_id,
            invited_username_lc=invited_username_lc,
            invited_user_id=invited_user_id,
            token=token,
            expires_at=expires_at,
        )
        invite_link = f"https://t.me/{self.bot_username}?start=join_{token}"
        return active, invitation, invite_link, invited_tg_user_id

    async def accept_invitation_by_token(
        self,
        *,
        token: str,
        accepter_tg_user_id: int,
        accepter_username: str | None,
    ) -> Pack:
        accepter_id = await self.touch_user(accepter_tg_user_id, accepter_username)
        accepter_username_lc = self.normalize_username(accepter_username)
        await self.db.expire_pending_invitations()

        invitation = await self.db.get_invitation_by_token(token)
        if not invitation:
            raise RuntimeError("Инвайт не найден")

        if invitation.status == InvitationStatus.ACCEPTED:
            raise RuntimeError("Этот инвайт уже был принят")
        if invitation.status == InvitationStatus.REVOKED:
            raise RuntimeError("Этот инвайт отозван")
        if invitation.status == InvitationStatus.EXPIRED:
            raise RuntimeError("Срок действия инвайта истек")

        if invitation.expires_at <= datetime.now(timezone.utc):
            await self.db.expire_invitation(invitation.id)
            raise RuntimeError("Срок действия инвайта истек")

        if invitation.invited_user_id is not None:
            if invitation.invited_user_id != accepter_id:
                raise RuntimeError("Этот инвайт предназначен другому пользователю")
        else:
            if not accepter_username_lc or accepter_username_lc != invitation.invited_username_lc:
                raise RuntimeError("Этот инвайт предназначен другому username")

        if not await self.db.is_pack_member(invitation.pack_id, accepter_id):
            editors_count = await self.db.count_editors(invitation.pack_id)
            if editors_count >= self.max_editors_per_pack:
                raise RuntimeError(f"Лимит редакторов достигнут ({self.max_editors_per_pack})")
            await self.db.accept_invitation(invitation.id, accepter_id)
        else:
            await self.db.revoke_invitation(invitation.id)

        active = await self.db.get_active_pack(accepter_id)
        if active is None:
            await self.db.set_active_pack_for_user(accepter_id, invitation.pack_id)

        pack = await self.db.get_pack_by_id(invitation.pack_id, requester_user_id=accepter_id)
        if not pack:
            raise RuntimeError("Не удалось получить пак после принятия инвайта")
        return pack

    async def members_for_active_pack(
        self,
        *,
        requester_tg_user_id: int,
        requester_username: str | None,
    ) -> tuple[Pack, MemberRole, list[PackMember], list[PackInvitation]]:
        requester_id = await self.touch_user(requester_tg_user_id, requester_username)
        await self.db.expire_pending_invitations()

        pack = await self.db.get_active_pack(requester_id)
        if not pack:
            raise RuntimeError("Нет активного пака. Выберите через /packs или создайте /newpack")

        role = await self.db.get_pack_role(pack.id, requester_id)
        if role is None:
            await self.db.clear_active_pack_for_user(requester_id)
            raise RuntimeError("Доступ к активному паку потерян. Выберите другой через /packs")

        members = await self.db.list_pack_members(pack.id)
        pending = await self.db.list_pending_invitations(pack.id)
        return pack, role, members, pending

    async def kick_member_from_active_pack(
        self,
        *,
        requester_tg_user_id: int,
        requester_username: str | None,
        target_member_id: int,
    ) -> tuple[Pack, PackMember]:
        requester_id = await self.touch_user(requester_tg_user_id, requester_username)
        pack = await self.db.get_active_pack(requester_id)
        if not pack:
            raise RuntimeError("Нет активного пака. Выберите через /packs или создайте /newpack")

        role = await self.db.get_pack_role(pack.id, requester_id)
        if role != MemberRole.OWNER:
            raise RuntimeError("Только владелец может удалять участников")

        if target_member_id == requester_id:
            raise RuntimeError("Владелец не может удалить сам себя")

        members = await self.db.list_pack_members(pack.id)
        target = next((member for member in members if member.user_id == target_member_id), None)
        if not target:
            raise RuntimeError("Участник не найден в этом паке")
        if target.role == MemberRole.OWNER:
            raise RuntimeError("Нельзя удалить владельца")

        removed = await self.db.remove_member(pack.id, target_member_id)
        if not removed:
            raise RuntimeError("Не удалось удалить участника")

        return pack, target
