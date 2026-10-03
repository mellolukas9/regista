"""The signed-in person's own account: sessions, recovery codes and password
(docs/specs/design-system.md 7.18). Always operates on the user's own tenant, whatever client
a platform admin has selected."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, SelfService
from regista_api.auth.flow import (
    check_secret_attempt,
    claim_step,
    fail_attempt,
    fresh_step,
    replace_recovery_codes,
)
from regista_api.auth.mail import send_password_changed
from regista_api.auth.sessions import revoke_user_sessions
from regista_api.core.errors import api_error
from regista_api.core.security import (
    hash_password_async,
    password_problem,
    verify_password_async,
)
from regista_api.core.useragent import describe_device
from regista_api.tenants.schemas import (
    ChangePasswordRequest,
    RegenerateCodesRequest,
    RegeneratedCodesResponse,
    RevokedResponse,
    SessionItem,
    SessionList,
)

router = APIRouter(prefix="/account", tags=["account"])

_ME = Depends(SelfService())

# Only sessions that are fully signed in and still alive are shown.
_LIVE = (
    "stage = 'active' AND revoked_at IS NULL AND expires_at > now()"
    " AND last_seen_at > now() - make_interval(hours => :idle)"
)


@router.get("/sessions", response_model=SessionList)
async def list_sessions(auth: Annotated[Auth, _ME]) -> SessionList:
    async with auth.own() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, user_agent, host(ip) AS ip, created_at, last_seen_at"  # noqa: S608
                    f" FROM sessions WHERE user_id = :u AND {_LIVE} ORDER BY last_seen_at DESC"
                ),
                {"u": auth.user.id, "idle": auth.state.settings.session_idle_hours},
            )
        ).all()
    return SessionList(
        items=[
            SessionItem(
                id=r.id,
                device=describe_device(r.user_agent),
                ip=r.ip,
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                is_current=r.id == auth.session.id,
            )
            for r in rows
        ]
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def end_session(session_id: uuid.UUID, auth: Annotated[Auth, _ME]) -> None:
    if session_id == auth.session.id:
        raise api_error(409, "cannot_end_current_session")
    async with auth.own() as db:
        ended = await db.execute(
            text(
                "UPDATE sessions SET revoked_at = now()"
                " WHERE id = :id AND user_id = :u AND revoked_at IS NULL RETURNING id"
            ),
            {"id": session_id, "u": auth.user.id},
        )
        if ended.first() is None:
            raise api_error(404, "session_not_found")
        await audit.record(
            db,
            tenant_id=auth.user.tenant_id,
            actor_type="user",
            actor_id=auth.user.id,
            action="auth.session_ended",
            target_type="session",
            target_id=session_id,
            ip=auth.ip,
        )


@router.post("/sessions/revoke-others", response_model=RevokedResponse)
async def revoke_other_sessions(auth: Annotated[Auth, _ME]) -> RevokedResponse:
    async with auth.own() as db:
        revoked = await revoke_user_sessions(db, auth.user.id, except_session_id=auth.session.id)
        await audit.record(
            db,
            tenant_id=auth.user.tenant_id,
            actor_type="user",
            actor_id=auth.user.id,
            action="auth.other_sessions_ended",
            metadata={"count": revoked},
            ip=auth.ip,
        )
    return RevokedResponse(revoked=revoked)


@router.post("/recovery-codes", response_model=RegeneratedCodesResponse)
async def regenerate_recovery_codes(
    body: RegenerateCodesRequest, auth: Annotated[Auth, _ME]
) -> RegeneratedCodesResponse:
    """New codes invalidate the old ones; the password is asked again first."""
    await check_secret_attempt(auth, "account-secret", str(auth.user.id))
    if not await verify_password_async(auth.user.password_hash, body.password):
        await fail_attempt(auth, "recovery_codes")
        raise api_error(422, "password_incorrect")
    async with auth.own() as db:
        codes = await replace_recovery_codes(db, auth)
        await audit.record(
            db,
            tenant_id=auth.user.tenant_id,
            actor_type="user",
            actor_id=auth.user.id,
            action="auth.recovery_codes_regenerated",
            ip=auth.ip,
        )
    return RegeneratedCodesResponse(recovery_codes=codes)


@router.post("/password", status_code=204)
async def change_password(body: ChangePasswordRequest, auth: Annotated[Auth, _ME]) -> None:
    """Current password and an authenticator code are both required, so a stolen session alone
    cannot take over the account. Wrong answers count toward the same lockout as the login."""
    await check_secret_attempt(auth, "account-secret", str(auth.user.id))

    password_ok = await verify_password_async(auth.user.password_hash, body.current_password)
    step = fresh_step(auth, body.code)
    if not password_ok:
        await fail_attempt(auth, "password_change")
        raise api_error(422, "password_incorrect")
    if step is None:
        await fail_attempt(auth, "password_change")
        raise api_error(422, "invalid_code")
    problem = password_problem(body.new_password, current_hash=auth.user.password_hash)
    if problem is not None:
        raise api_error(422, problem)

    new_hash = await hash_password_async(body.new_password)
    async with auth.own() as db:
        if not await claim_step(db, auth, step):
            raise api_error(422, "invalid_code")
        await db.execute(
            text(
                "UPDATE users SET password_hash = :h, failed_logins = 0, locked_until = NULL,"
                " updated_at = now() WHERE id = :id"
            ),
            {"h": new_hash, "id": auth.user.id},
        )
        await revoke_user_sessions(db, auth.user.id, except_session_id=auth.session.id)
        await audit.record(
            db,
            tenant_id=auth.user.tenant_id,
            actor_type="user",
            actor_id=auth.user.id,
            action="auth.password_changed",
            ip=auth.ip,
        )
    await send_password_changed(auth.state, to=auth.user.email)
