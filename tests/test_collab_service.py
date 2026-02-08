import pytest

from app.db.models import InvitationStatus, MemberRole
from app.db.repo import Database
from app.services.collab_service import CollabService


@pytest.mark.asyncio
async def test_invite_accept_adds_editor_and_shared_pack(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    owner_tg = 1001
    editor_tg = 1002
    owner_id = await db.ensure_user(owner_tg, "owner_user")
    editor_id = await db.ensure_user(editor_tg, "editor_user")
    pack = await db.create_draft_pack(owner_id, "Shared", "shared_pack_by_bot")

    svc = CollabService(db=db, bot_username="mybot")
    _, invitation, link, invited_tg = await svc.create_invitation_for_active_pack(
        requester_tg_user_id=owner_tg,
        requester_username="owner_user",
        invited_username_raw="@editor_user",
    )
    assert invited_tg == editor_tg
    assert f"join_{invitation.token}" in link

    joined_pack = await svc.accept_invitation_by_token(
        token=invitation.token,
        accepter_tg_user_id=editor_tg,
        accepter_username="editor_user",
    )
    assert joined_pack.id == pack.id

    role = await db.get_pack_role(pack.id, editor_id)
    assert role == MemberRole.EDITOR

    saved_invitation = await db.get_invitation_by_token(invitation.token)
    assert saved_invitation is not None
    assert saved_invitation.status == InvitationStatus.ACCEPTED

    editor_packs = await db.list_packs_for_user(editor_id)
    assert any(p.id == pack.id and p.role == MemberRole.EDITOR for p in editor_packs)

    editor_active = await db.get_active_pack(editor_id)
    assert editor_active is not None
    assert editor_active.id == pack.id

    await db.close()


@pytest.mark.asyncio
async def test_kick_owner_only_and_removes_active_pack_for_kicked_user(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    owner_tg = 2001
    editor_tg = 2002
    owner_id = await db.ensure_user(owner_tg, "owner_two")
    editor_id = await db.ensure_user(editor_tg, "editor_two")
    pack = await db.create_draft_pack(owner_id, "Shared", "shared_pack_2_by_bot")

    svc = CollabService(db=db, bot_username="mybot")
    _, invitation, _, _ = await svc.create_invitation_for_active_pack(
        requester_tg_user_id=owner_tg,
        requester_username="owner_two",
        invited_username_raw="@editor_two",
    )
    await svc.accept_invitation_by_token(
        token=invitation.token,
        accepter_tg_user_id=editor_tg,
        accepter_username="editor_two",
    )

    await db.set_active_pack_for_user(editor_id, pack.id)
    with pytest.raises(RuntimeError, match="Только владелец"):
        await svc.kick_member_from_active_pack(
            requester_tg_user_id=editor_tg,
            requester_username="editor_two",
            target_member_id=owner_id,
        )

    await svc.kick_member_from_active_pack(
        requester_tg_user_id=owner_tg,
        requester_username="owner_two",
        target_member_id=editor_id,
    )
    assert await db.get_pack_role(pack.id, editor_id) is None
    assert await db.get_active_pack(editor_id) is None

    await db.close()


@pytest.mark.asyncio
async def test_expired_invitation_becomes_expired_status(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.initialize()

    owner_tg = 3001
    invited_tg = 3002
    owner_id = await db.ensure_user(owner_tg, "owner_three")
    await db.ensure_user(invited_tg, "invited_three")
    await db.create_draft_pack(owner_id, "Shared", "shared_pack_3_by_bot")

    svc = CollabService(db=db, bot_username="mybot")
    _, invitation, _, _ = await svc.create_invitation_for_active_pack(
        requester_tg_user_id=owner_tg,
        requester_username="owner_three",
        invited_username_raw="@invited_three",
    )

    assert db.conn is not None
    await db.conn.execute(
        "UPDATE pack_invitations SET expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", invitation.id),
    )
    await db.conn.commit()

    with pytest.raises(RuntimeError, match="Срок действия инвайта истек"):
        await svc.accept_invitation_by_token(
            token=invitation.token,
            accepter_tg_user_id=invited_tg,
            accepter_username="invited_three",
        )

    saved_invitation = await db.get_invitation_by_token(invitation.token)
    assert saved_invitation is not None
    assert saved_invitation.status == InvitationStatus.EXPIRED

    await db.close()
