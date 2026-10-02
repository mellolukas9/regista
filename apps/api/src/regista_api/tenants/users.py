"""Users of a client: list, invite, change role, end sessions, remove access, re-invite
(docs/specs/design-system.md 7.17 and section 11.1).

Reads follow the client context (`auth.scoped()`); every write goes through `auth.writing()`,
which refuses "all clients". Isolation between clients comes from RLS: a user id from another
client is simply not found (404).
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.invitations import issue_invitation
from regista_api.auth.mail import send_invitation
from regista_api.auth.permissions import Permission
from regista_api.auth.sessions import revoke_user_sessions
from regista_api.core.errors import api_error
from regista_api.core.pagination import Pagination, like_pattern, order_by
from regista_api.tenants.schemas import (
    ChangeRoleRequest,
    InviteUserRequest,
    RevokedResponse,
    UserItem,
    UserList,
    UserSummary,
)

router = APIRouter(prefix="/users", tags=["users"])

_MANAGE = Depends(Require(Permission.USERS_MANAGE))

# ORDER BY expressions are fixed here; user input only picks a key (core/pagination.py).
_SORT = {
    "email": "lower(u.email::text)",
    "role": "u.role",
    "status": "u.status",
    "last_login_at": "u.last_login_at",
}

# Staff (internal tenant) never show up here: they are managed with the regista-admin CLI.
_WHERE = (
    "NOT t.is_internal"
    " AND (CAST(:status AS text) IS NULL AND u.status <> 'disabled' OR u.status = :status)"
    " AND (CAST(:role AS text) IS NULL OR u.role = :role)"
    " AND (CAST(:like AS text) IS NULL OR u.email::text ILIKE :like ESCAPE '\\')"
)

_RESET_CREDENTIALS = (
    "password_hash = NULL, status = 'invited', mfa_enabled = false, mfa_secret_enc = NULL,"
    " mfa_key_id = NULL, mfa_enabled_at = NULL, mfa_last_step = NULL, failed_logins = 0,"
    " locked_until = NULL, updated_at = now()"
)


@router.get("", response_model=UserList)
async def list_users(
    auth: Annotated[Auth, _MANAGE],
    paging: Pagination,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Annotated[Literal["tenant_admin", "operator", "viewer"] | None, Query()] = None,
    status: Annotated[Literal["active", "invited", "disabled"] | None, Query()] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> UserList:
    ordering = order_by(sort, _SORT, "email")
    params = {"status": status, "role": role, "like": like_pattern(q)}
    base = "FROM users u JOIN tenants t ON t.id = u.tenant_id"
    async with auth.scoped() as db:
        total: int = (
            await db.execute(text(f"SELECT count(*) {base} WHERE {_WHERE}"), params)
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    "SELECT u.id, u.email::text AS email, u.role, u.status, u.mfa_enabled,"
                    " u.last_login_at, u.tenant_id, t.name AS client_name"
                    f" {base} WHERE {_WHERE} ORDER BY {ordering} LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": paging.per_page, "offset": paging.offset},
            )
        ).all()
    return UserList(
        items=[
            UserItem(
                id=r.id,
                email=r.email,
                role=r.role,
                status=r.status,
                mfa=None if r.status == "invited" else ("active" if r.mfa_enabled else "none"),
                last_login_at=r.last_login_at,
                client_id=r.tenant_id,
                client_name=r.client_name,
                is_self=r.id == auth.user.id,
            )
            for r in rows
        ],
        total=total,
        page=paging.page,
        per_page=paging.per_page,
    )


@router.post("/invitations", response_model=UserSummary, status_code=201)
async def invite_user(body: InviteUserRequest, auth: Annotated[Auth, _MANAGE]) -> UserSummary:
    state = auth.state
    try:
        async with auth.writing() as db:
            existing = (
                await db.execute(
                    text("SELECT id, status FROM users WHERE email = :e FOR UPDATE"),
                    {"e": body.email},
                )
            ).first()
            if existing is not None and existing.status != "disabled":
                raise api_error(409, "already_has_access")
            tenant_id = auth.client_id
            if existing is not None:
                # "Remover acesso" asked for a new invitation to come back: start from scratch.
                user_id = existing.id
                await db.execute(
                    text(f"UPDATE users SET role = :r, {_RESET_CREDENTIALS} WHERE id = :id"),  # noqa: S608
                    {"r": body.role, "id": user_id},
                )
                await db.execute(
                    text("DELETE FROM recovery_codes WHERE user_id = :u"), {"u": user_id}
                )
                await revoke_user_sessions(db, user_id)
            else:
                user_id = (
                    await db.execute(
                        text(
                            "INSERT INTO users (tenant_id, email, role) VALUES (:t, :e, :r)"
                            " RETURNING id"
                        ),
                        {"t": tenant_id, "e": body.email, "r": body.role},
                    )
                ).scalar_one()
            token = await issue_invitation(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                created_by=auth.user.id,
                settings=state.settings,
            )
            await _audit(
                db,
                auth,
                "user.invited",
                user_id,
                {"role": body.role, "reactivated": existing is not None},
            )
    except IntegrityError as exc:
        if "uq_users_email" in str(exc.orig):
            # The address belongs to someone in another client; do not say who or where.
            raise api_error(409, "email_in_use") from None
        raise
    await send_invitation(state, to=body.email, token=token)
    return UserSummary(id=user_id, email=body.email, role=body.role, status="invited")


@router.patch("/{user_id}", response_model=UserSummary)
async def change_role(
    user_id: uuid.UUID, body: ChangeRoleRequest, auth: Annotated[Auth, _MANAGE]
) -> UserSummary:
    async with auth.writing() as db:
        target = await _target(db, user_id)
        if target.id == auth.user.id:
            raise api_error(409, "cannot_change_own_role")
        if target.status == "disabled":
            raise api_error(409, "user_disabled")
        if body.role != "tenant_admin":
            await _ensure_not_last_admin(db, target)
        if body.role != target.role:
            await db.execute(
                text("UPDATE users SET role = :r, updated_at = now() WHERE id = :id"),
                {"r": body.role, "id": user_id},
            )
            await _audit(
                db, auth, "user.role_changed", user_id, {"from": target.role, "to": body.role}
            )
    return UserSummary(id=user_id, email=target.email, role=body.role, status=target.status)


@router.post("/{user_id}/revoke-sessions", response_model=RevokedResponse)
async def revoke_sessions(user_id: uuid.UUID, auth: Annotated[Auth, _MANAGE]) -> RevokedResponse:
    """Signs the person out everywhere. Robots keep running: sessions are only about people."""
    async with auth.writing() as db:
        await _target(db, user_id)
        revoked = await revoke_user_sessions(db, user_id)
        await _audit(db, auth, "user.sessions_revoked", user_id, {"count": revoked})
    return RevokedResponse(revoked=revoked)


@router.post("/{user_id}/remove-access", response_model=UserSummary)
async def remove_access(user_id: uuid.UUID, auth: Annotated[Auth, _MANAGE]) -> UserSummary:
    async with auth.writing() as db:
        target = await _target(db, user_id)
        if target.id == auth.user.id:
            raise api_error(409, "cannot_remove_own_access")
        if target.status != "disabled":
            await _ensure_not_last_admin(db, target)
            await db.execute(
                text("UPDATE users SET status = 'disabled', updated_at = now() WHERE id = :id"),
                {"id": user_id},
            )
            await db.execute(
                text(
                    "UPDATE invitations SET revoked_at = now()"
                    " WHERE user_id = :u AND used_at IS NULL AND revoked_at IS NULL"
                ),
                {"u": user_id},
            )
            await revoke_user_sessions(db, user_id)
            await _audit(db, auth, "user.access_removed", user_id, {})
    return UserSummary(id=user_id, email=target.email, role=target.role, status="disabled")


@router.post("/{user_id}/resend-invitation", response_model=UserSummary)
async def resend_invitation(user_id: uuid.UUID, auth: Annotated[Auth, _MANAGE]) -> UserSummary:
    """The only way back for someone who forgot the password (ADR 0017): the current password,
    MFA, recovery codes and sessions all stop working and a fresh invitation is sent.

    Unlike removing access or changing the role, this is allowed on the last Admin of a client:
    that is exactly the person who most needs it, and the Artemisys team can always run it.
    """
    state = auth.state
    async with auth.writing() as db:
        target = await _target(db, user_id)
        if target.id == auth.user.id:
            raise api_error(409, "cannot_reinvite_self")
        if target.status == "disabled":
            raise api_error(409, "user_disabled")
        tenant_id = auth.client_id
        await db.execute(
            text(f"UPDATE users SET {_RESET_CREDENTIALS} WHERE id = :id"),  # noqa: S608
            {"id": user_id},
        )
        await db.execute(text("DELETE FROM recovery_codes WHERE user_id = :u"), {"u": user_id})
        await revoke_user_sessions(db, user_id)
        token = await issue_invitation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            created_by=auth.user.id,
            settings=state.settings,
        )
        await _audit(db, auth, "user.reinvited", user_id, {})
    await send_invitation(state, to=target.email, token=token, reinvite=True)
    return UserSummary(id=user_id, email=target.email, role=target.role, status="invited")


# --- helpers ----------------------------------------------------------------------------------


async def _target(db: AsyncSession, user_id: uuid.UUID) -> Row[Any]:
    """The user in the current client (RLS hides everyone else), locked for the change."""
    row = (
        await db.execute(
            text(
                "SELECT id, email::text AS email, role, status, tenant_id FROM users"
                " WHERE id = :id FOR UPDATE"
            ),
            {"id": user_id},
        )
    ).first()
    if row is None:
        raise api_error(404, "user_not_found")
    return row


async def _ensure_not_last_admin(db: AsyncSession, target: Row[Any]) -> None:
    """A client keeps at least one active Admin. The rows are locked so two concurrent changes
    cannot both pass the check."""
    if target.role != "tenant_admin" or target.status != "active":
        return
    admins = [
        r[0]
        for r in await db.execute(
            text(
                "SELECT id FROM users WHERE tenant_id = :t AND role = 'tenant_admin'"
                " AND status = 'active' FOR UPDATE"
            ),
            {"t": target.tenant_id},
        )
    ]
    if not [a for a in admins if a != target.id]:
        raise api_error(409, "last_admin")


async def _audit(
    db: AsyncSession, auth: Auth, action: str, user_id: uuid.UUID, metadata: dict[str, object]
) -> None:
    await audit.record(
        db,
        tenant_id=auth.client_id,
        actor_type="user",
        actor_id=auth.user.id,
        action=action,
        target_type="user",
        target_id=user_id,
        metadata=metadata,
        ip=auth.ip,
    )
