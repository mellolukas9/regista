"""Login, invitation acceptance, MFA and session routes (docs/specs/security.md, ADR 0005/0017)."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth import mfa
from regista_api.auth.deps import (
    Auth,
    PublicRoute,
    RequireStage,
    api_error,
    get_state,
)
from regista_api.auth.lockout import is_locked, record_failure
from regista_api.auth.rate_limit import client_ip, rate_limited
from regista_api.auth.schemas import (
    AcceptInvitationRequest,
    CodeRequest,
    InvitationInspectResponse,
    InvitationTokenRequest,
    LoginRequest,
    MeResponse,
    MfaSetupResponse,
    RecoveryCodeRequest,
    RecoveryCodesResponse,
    StageResponse,
)
from regista_api.auth.sessions import (
    IssuedSession,
    apply_cookies,
    clear_cookies,
    create_session,
    revoke_session,
    rotate_session,
)
from regista_api.core.db import tenant_session
from regista_api.core.security import (
    hash_password,
    hash_password_async,
    hash_token,
    needs_rehash,
    new_recovery_code,
    normalize_recovery_code,
    password_problem,
    verify_password_async,
)

router = APIRouter(prefix="/auth", tags=["auth"])

RECOVERY_CODE_COUNT = 10


class _InvalidInvitation(Exception):
    """Raised inside a transaction to roll it back."""


async def _limit(request: Request, scope: str, subject: str, window: int, max_hits: int) -> None:
    state = get_state(request)
    if await rate_limited(
        state.factory, scope=scope, subject=subject, window_seconds=window, max_hits=max_hits
    ):
        raise api_error(429, "rate_limited")


async def _promote(db: AsyncSession, auth: Auth, method: str) -> IssuedSession:
    """Finish the login: new token, session becomes active, failures are forgiven."""
    issued = await rotate_session(
        db, session_id=auth.session.id, stage="active", settings=auth.state.settings
    )
    await db.execute(
        text(
            "UPDATE users SET last_login_at = now(), failed_logins = 0, locked_until = NULL,"
            " updated_at = now() WHERE id = :id"
        ),
        {"id": auth.user.id},
    )
    await audit.record(
        db,
        tenant_id=auth.user.tenant_id,
        actor_type="user",
        actor_id=auth.user.id,
        action="auth.login",
        metadata={"method": method},
        ip=auth.ip,
    )
    return issued


async def _fail_attempt(auth: Auth) -> None:
    await record_failure(
        auth.state.factory,
        auth.state.settings,
        tenant_id=auth.user.tenant_id,
        user_id=auth.user.id,
        stage=auth.session.stage,
        ip=auth.ip,
    )


async def _check_mfa_attempt(request: Request, auth: Auth) -> None:
    await _limit(request, "mfa", str(auth.session.id), 300, auth.state.settings.rate_mfa_per_5min)
    if is_locked(auth.user.locked_until):
        raise api_error(429, "rate_limited")


def _totp_secret(auth: Auth) -> str:
    user = auth.user
    if not user.mfa_secret_enc or not user.mfa_key_id:
        raise api_error(409, "mfa_not_configured")
    return mfa.decrypt_secret(
        auth.state.keys, user.tenant_id, user.id, user.mfa_secret_enc, user.mfa_key_id
    )


def _fresh_step(auth: Auth, code: str) -> int | None:
    """The TOTP step if the code is valid and newer than the last accepted one."""
    step = mfa.match_step(_totp_secret(auth), code)
    last = auth.user.mfa_last_step
    return step if step is not None and (last is None or step > last) else None


async def _claim_step(db: AsyncSession, auth: Auth, step: int) -> bool:
    """Atomically record the step; false when a concurrent request already used it."""
    result = await db.execute(
        text(
            "UPDATE users SET mfa_last_step = :step, updated_at = now() WHERE id = :id"
            " AND (mfa_last_step IS NULL OR mfa_last_step < :step) RETURNING id"
        ),
        {"id": auth.user.id, "step": step},
    )
    return result.first() is not None


async def _replace_recovery_codes(db: AsyncSession, auth: Auth) -> list[str]:
    codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    hashes = await asyncio.to_thread(
        lambda: [hash_password(normalize_recovery_code(c)) for c in codes]
    )
    await db.execute(text("DELETE FROM recovery_codes WHERE user_id = :u"), {"u": auth.user.id})
    await db.execute(
        text("INSERT INTO recovery_codes (tenant_id, user_id, code_hash) VALUES (:t, :u, :h)"),
        [{"t": auth.user.tenant_id, "u": auth.user.id, "h": h} for h in hashes],
    )
    return codes


# --- public routes ----------------------------------------------------------------------------


@router.post("/login", response_model=StageResponse, dependencies=[Depends(PublicRoute())])
async def login(body: LoginRequest, request: Request, response: Response) -> StageResponse:
    state = get_state(request)
    s = state.settings
    ip = client_ip(request, s)
    await _limit(request, "login-ip", ip or "unknown", 60, s.rate_login_ip_per_minute)
    await _limit(request, "login-email", body.email, 900, s.rate_login_email_per_15min)

    async with tenant_session(state.factory) as db:
        row = (
            (await db.execute(text("SELECT * FROM app.lookup_login(:e)"), {"e": body.email}))
            .mappings()
            .first()
        )

    # Unknown, disabled and never-activated accounts still cost one argon2 verification, so
    # response time does not reveal which case it was.
    usable = (
        row is not None
        and row["status"] == "active"
        and bool(row["password_hash"])
        and row["tenant_active"]
    )
    if row is not None and usable and is_locked(row["locked_until"]):
        raise api_error(429, "rate_limited")
    ok = await verify_password_async(
        row["password_hash"] if usable and row else None, body.password
    )
    if not ok or row is None:
        if usable and row is not None:
            await record_failure(
                state.factory,
                s,
                tenant_id=row["tenant_id"],
                user_id=row["id"],
                stage="password",
                ip=ip,
            )
        raise api_error(401, "invalid_credentials")

    stage = "mfa_required" if row["mfa_enabled"] else "mfa_setup"
    async with tenant_session(state.factory, tenant_id=row["tenant_id"]) as db:
        if needs_rehash(row["password_hash"]):
            await db.execute(
                text("UPDATE users SET password_hash = :h, updated_at = now() WHERE id = :id"),
                {"h": await hash_password_async(body.password), "id": row["id"]},
            )
        issued = await create_session(
            db,
            tenant_id=row["tenant_id"],
            user_id=row["id"],
            stage=stage,
            settings=s,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    apply_cookies(response, s, issued)
    return StageResponse(stage=stage)


@router.post(
    "/invitations/inspect",
    response_model=InvitationInspectResponse,
    dependencies=[Depends(PublicRoute())],
)
async def inspect_invitation(
    body: InvitationTokenRequest, request: Request
) -> InvitationInspectResponse:
    state = get_state(request)
    s = state.settings
    await _limit(
        request, "invite-ip", client_ip(request, s) or "unknown", 60, s.rate_invite_ip_per_minute
    )
    async with tenant_session(state.factory) as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT *, (expires_at > now() AND used_at IS NULL AND revoked_at IS NULL)"
                        " AS valid FROM app.lookup_invitation(:h)"
                    ),
                    {"h": hash_token(body.token)},
                )
            )
            .mappings()
            .first()
        )
    if row is None or not row["valid"]:
        raise api_error(404, "invitation_invalid")
    return InvitationInspectResponse(email=row["email"])


@router.post(
    "/invitations/accept", response_model=StageResponse, dependencies=[Depends(PublicRoute())]
)
async def accept_invitation(
    body: AcceptInvitationRequest, request: Request, response: Response
) -> StageResponse:
    state = get_state(request)
    s = state.settings
    ip = client_ip(request, s)
    await _limit(request, "invite-ip", ip or "unknown", 60, s.rate_invite_ip_per_minute)

    async with tenant_session(state.factory) as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM app.lookup_invitation(:h)"), {"h": hash_token(body.token)}
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise api_error(404, "invitation_invalid")

    problem = password_problem(body.password)
    if problem is not None:
        raise api_error(422, problem)
    password_hash = await hash_password_async(body.password)

    try:
        async with tenant_session(state.factory, tenant_id=row["tenant_id"]) as db:
            # Single use, atomically: only one request can flip `used_at`.
            claimed = await db.execute(
                text(
                    "UPDATE invitations SET used_at = now() WHERE id = :id AND used_at IS NULL"
                    " AND revoked_at IS NULL AND expires_at > now() RETURNING id"
                ),
                {"id": row["invitation_id"]},
            )
            activated = await db.execute(
                text(
                    "UPDATE users SET password_hash = :h, status = 'active', mfa_enabled = false,"
                    " mfa_secret_enc = NULL, mfa_key_id = NULL, mfa_enabled_at = NULL,"
                    " mfa_last_step = NULL, failed_logins = 0, locked_until = NULL,"
                    " updated_at = now() WHERE id = :u AND status = 'invited' RETURNING id"
                ),
                {"h": password_hash, "u": row["user_id"]},
            )
            if claimed.first() is None or activated.first() is None:
                raise _InvalidInvitation
            issued = await create_session(
                db,
                tenant_id=row["tenant_id"],
                user_id=row["user_id"],
                stage="mfa_setup",
                settings=s,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
            await audit.record(
                db,
                tenant_id=row["tenant_id"],
                actor_type="user",
                actor_id=row["user_id"],
                action="user.invitation_accepted",
                target_type="user",
                target_id=row["user_id"],
                ip=ip,
            )
    except _InvalidInvitation:
        raise api_error(404, "invitation_invalid") from None
    apply_cookies(response, s, issued)
    return StageResponse(stage="mfa_setup")


# --- session in progress or active ------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
async def me(auth: Annotated[Auth, Depends(RequireStage())]) -> MeResponse:
    user = auth.user
    if auth.session.stage != "active":
        return MeResponse(stage=auth.session.stage, email=user.email)
    async with auth.own() as db:
        remaining: int = (
            await db.execute(
                text("SELECT count(*) FROM recovery_codes WHERE user_id = :u AND used_at IS NULL"),
                {"u": user.id},
            )
        ).scalar_one()
    return MeResponse(
        stage="active",
        email=user.email,
        role=user.role,
        display_name=user.display_name,
        is_platform_admin=user.is_platform_admin,
        tenant_id=str(user.tenant_id),
        tenant_name=user.tenant_name,
        mfa_enabled=user.mfa_enabled,
        mfa_enabled_at=user.mfa_enabled_at,
        recovery_codes_remaining=remaining,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response, auth: Annotated[Auth, Depends(RequireStage())]) -> None:
    async with auth.own() as db:
        await revoke_session(db, auth.session.id)
        if auth.session.stage == "active":
            await audit.record(
                db,
                tenant_id=auth.user.tenant_id,
                actor_type="user",
                actor_id=auth.user.id,
                action="auth.logout",
                ip=auth.ip,
            )
    clear_cookies(response, auth.state.settings)


# --- MFA --------------------------------------------------------------------------------------


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(auth: Annotated[Auth, Depends(RequireStage("mfa_setup"))]) -> MfaSetupResponse:
    """Create (or replace, if the page was reloaded) the pending TOTP secret."""
    if auth.user.mfa_enabled:
        raise api_error(409, "mfa_already_enabled")
    secret = mfa.new_secret()
    ciphertext, key_id = mfa.encrypt_secret(
        auth.state.keys, auth.user.tenant_id, auth.user.id, secret
    )
    async with auth.own() as db:
        await db.execute(
            text(
                "UPDATE users SET mfa_secret_enc = :c, mfa_key_id = :k, updated_at = now()"
                " WHERE id = :id"
            ),
            {"c": ciphertext, "k": key_id, "id": auth.user.id},
        )
    return MfaSetupResponse(
        secret=secret, otpauth_uri=mfa.provisioning_uri(secret, auth.user.email)
    )


@router.post("/mfa/activate", response_model=RecoveryCodesResponse)
async def mfa_activate(
    body: CodeRequest,
    request: Request,
    response: Response,
    auth: Annotated[Auth, Depends(RequireStage("mfa_setup"))],
) -> RecoveryCodesResponse:
    await _check_mfa_attempt(request, auth)
    if auth.user.mfa_enabled:
        raise api_error(409, "mfa_already_enabled")
    step = _fresh_step(auth, body.code)
    codes: list[str] = []
    issued: IssuedSession | None = None
    if step is not None:
        async with auth.own() as db:
            if await _claim_step(db, auth, step):
                await db.execute(
                    text(
                        "UPDATE users SET mfa_enabled = true, mfa_enabled_at = now(),"
                        " updated_at = now() WHERE id = :id"
                    ),
                    {"id": auth.user.id},
                )
                codes = await _replace_recovery_codes(db, auth)
                issued = await rotate_session(
                    db,
                    session_id=auth.session.id,
                    stage="recovery_codes",
                    settings=auth.state.settings,
                )
                await audit.record(
                    db,
                    tenant_id=auth.user.tenant_id,
                    actor_type="user",
                    actor_id=auth.user.id,
                    action="auth.mfa_enabled",
                    ip=auth.ip,
                )
    if issued is None:
        await _fail_attempt(auth)
        raise api_error(401, "invalid_code")
    apply_cookies(response, auth.state.settings, issued)
    return RecoveryCodesResponse(stage="recovery_codes", recovery_codes=codes)


@router.post("/mfa/verify", response_model=StageResponse)
async def mfa_verify(
    body: CodeRequest,
    request: Request,
    response: Response,
    auth: Annotated[Auth, Depends(RequireStage("mfa_required"))],
) -> StageResponse:
    await _check_mfa_attempt(request, auth)
    step = _fresh_step(auth, body.code)
    issued: IssuedSession | None = None
    if step is not None:
        async with auth.own() as db:
            if await _claim_step(db, auth, step):
                issued = await _promote(db, auth, "totp")
    if issued is None:
        await _fail_attempt(auth)
        raise api_error(401, "invalid_code")
    apply_cookies(response, auth.state.settings, issued)
    return StageResponse(stage="active")


@router.post("/mfa/recover", response_model=StageResponse)
async def mfa_recover(
    body: RecoveryCodeRequest,
    request: Request,
    response: Response,
    auth: Annotated[Auth, Depends(RequireStage("mfa_required"))],
) -> StageResponse:
    await _check_mfa_attempt(request, auth)
    candidate = normalize_recovery_code(body.recovery_code)
    async with auth.own() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, code_hash FROM recovery_codes"
                    " WHERE user_id = :u AND used_at IS NULL"
                ),
                {"u": auth.user.id},
            )
        ).all()
    matched: uuid.UUID | None = None
    for code_id, code_hash in rows:
        # Check every unused code so timing does not reveal which one matched.
        if await verify_password_async(code_hash, candidate) and matched is None:
            matched = code_id

    issued: IssuedSession | None = None
    if matched is not None:
        async with auth.own() as db:
            used = await db.execute(
                text(
                    "UPDATE recovery_codes SET used_at = now() WHERE id = :id AND used_at IS NULL"
                    " RETURNING id"
                ),
                {"id": matched},
            )
            if used.first() is not None:
                issued = await _promote(db, auth, "recovery_code")
    if issued is None:
        await _fail_attempt(auth)
        raise api_error(401, "invalid_recovery_code")
    apply_cookies(response, auth.state.settings, issued)
    return StageResponse(stage="active")


@router.post("/recovery-codes/reissue", response_model=RecoveryCodesResponse)
async def reissue_recovery_codes(
    auth: Annotated[Auth, Depends(RequireStage("recovery_codes"))],
) -> RecoveryCodesResponse:
    """The codes are shown once; if the page is reloaded before confirming, make a new set."""
    async with auth.own() as db:
        codes = await _replace_recovery_codes(db, auth)
    return RecoveryCodesResponse(stage="recovery_codes", recovery_codes=codes)


@router.post("/recovery-codes/ack", response_model=StageResponse)
async def acknowledge_recovery_codes(
    response: Response, auth: Annotated[Auth, Depends(RequireStage("recovery_codes"))]
) -> StageResponse:
    async with auth.own() as db:
        issued = await _promote(db, auth, "mfa_setup")
    apply_cookies(response, auth.state.settings, issued)
    return StageResponse(stage="active")
