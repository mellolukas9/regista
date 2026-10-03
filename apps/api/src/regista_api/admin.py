"""Operations run by the Artemisys team from the command line (`regista-admin`).

The Artemisys staff live in the hidden internal tenant and have no screen in the panel for
now (docs/STATUS.md). They are created and removed here, always through an invitation: the CLI
never accepts a password. Everything goes through the runtime role (`regista_app`) and RLS.
"""

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.audit import service as audit
from regista_api.auth import mfa
from regista_api.auth.deps import AppState
from regista_api.auth.invitations import issue_invitation
from regista_api.auth.mail import send_invitation
from regista_api.auth.sessions import revoke_user_sessions
from regista_api.core.db import tenant_session
from regista_api.core.security import (
    hash_password,
    new_recovery_code,
    normalize_recovery_code,
)

INTERNAL_NAME = "Artemisys"
INTERNAL_SLUG = "artemisys-equipe"
DEFAULT_REGION = "sa-east-1"


class AdminError(Exception):
    """A refusal the operator should read (the message is shown as is)."""


Factory = async_sessionmaker[AsyncSession]

_RESET = (
    "password_hash = NULL, status = 'invited', mfa_enabled = false, mfa_secret_enc = NULL,"
    " mfa_key_id = NULL, mfa_enabled_at = NULL, mfa_last_step = NULL, failed_logins = 0,"
    " locked_until = NULL, updated_at = now()"
)


async def _internal_tenant(db: AsyncSession) -> uuid.UUID:
    """The internal tenant, created on first use (platform-admin context required)."""
    found = (await db.execute(text("SELECT id FROM tenants WHERE is_internal"))).scalar()
    if found is not None:
        return uuid.UUID(str(found))
    created: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO tenants (name, slug, data_region, is_internal)"
                " VALUES (:n, :s, :r, true) RETURNING id"
            ),
            {"n": INTERNAL_NAME, "s": INTERNAL_SLUG, "r": DEFAULT_REGION},
        )
    ).scalar_one()
    return created


async def _enter_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Same transaction, now writing inside `tenant_id` (RLS WITH CHECK)."""
    await db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})


async def create_platform_admin(
    factory: Factory, state: AppState, *, email: str, name: str
) -> uuid.UUID:
    email = email.strip().lower()
    async with tenant_session(factory, platform_admin=True) as db:
        tenant_id = await _internal_tenant(db)
        await _enter_tenant(db, tenant_id)
        taken = (
            await db.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email})
        ).first()
        if taken:
            raise AdminError(
                "Este e-mail já existe. Para quem já é da equipe, use resend-platform-admin-invite."
            )
        user_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO users (tenant_id, email, role, is_platform_admin, display_name)"
                    " VALUES (:t, :e, 'tenant_admin', true, :n) RETURNING id"
                ),
                {"t": tenant_id, "e": email, "n": name.strip() or None},
            )
        ).scalar_one()
        token = await issue_invitation(
            db, tenant_id=tenant_id, user_id=user_id, created_by=None, settings=state.settings
        )
        await audit.record(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=None,
            action="platform_admin.created",
            target_type="user",
            target_id=user_id,
        )
    await send_invitation(state, to=email, token=token)
    return user_id


async def _platform_admin(db: AsyncSession, email: str) -> Row[Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, tenant_id, status FROM users"
                " WHERE email = :e AND is_platform_admin FOR UPDATE"
            ),
            {"e": email.strip().lower()},
        )
    ).first()
    if row is None:
        raise AdminError("Não existe administrador da plataforma com esse e-mail.")
    return row


async def resend_platform_admin_invite(factory: Factory, state: AppState, *, email: str) -> None:
    async with tenant_session(factory, platform_admin=True) as db:
        tenant_id = await _internal_tenant(db)
        await _enter_tenant(db, tenant_id)
        row = await _platform_admin(db, email)
        if row.status == "disabled":
            raise AdminError("Esse acesso foi removido. Crie o administrador de novo.")
        await db.execute(text(f"UPDATE users SET {_RESET} WHERE id = :id"), {"id": row.id})  # noqa: S608
        await db.execute(text("DELETE FROM recovery_codes WHERE user_id = :u"), {"u": row.id})
        await revoke_user_sessions(db, row.id)
        token = await issue_invitation(
            db, tenant_id=tenant_id, user_id=row.id, created_by=None, settings=state.settings
        )
        await audit.record(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=None,
            action="platform_admin.reinvited",
            target_type="user",
            target_id=row.id,
        )
    await send_invitation(state, to=email.strip().lower(), token=token, reinvite=True)


async def remove_platform_admin(factory: Factory, *, email: str) -> None:
    async with tenant_session(factory, platform_admin=True) as db:
        tenant_id = await _internal_tenant(db)
        await _enter_tenant(db, tenant_id)
        row = await _platform_admin(db, email)
        if row.status == "disabled":
            return
        if row.status == "active":
            others: int = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM users WHERE tenant_id = :t AND is_platform_admin"
                        " AND status = 'active' AND id <> :id"
                    ),
                    {"t": tenant_id, "id": row.id},
                )
            ).scalar_one()
            if others == 0:
                raise AdminError(
                    "Esse é o último administrador da plataforma ativo. "
                    "Crie outro (create-platform-admin) antes de remover este."
                )
        await db.execute(
            text("UPDATE users SET status = 'disabled', updated_at = now() WHERE id = :id"),
            {"id": row.id},
        )
        await db.execute(
            text(
                "UPDATE invitations SET revoked_at = now()"
                " WHERE user_id = :u AND used_at IS NULL AND revoked_at IS NULL"
            ),
            {"u": row.id},
        )
        await revoke_user_sessions(db, row.id)
        await audit.record(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=None,
            action="platform_admin.removed",
            target_type="user",
            target_id=row.id,
        )


# --- development seed (docs/specs/design-system.md, section 10) -------------------------------


@dataclass
class SeedUser:
    email: str
    role: str
    client: str
    password: str | None = None
    totp_secret: str | None = None
    otpauth_uri: str | None = None
    recovery_codes: list[str] = field(default_factory=list)
    invitation_sent: bool = False


@dataclass
class SeedResult:
    users: list[SeedUser]


_EXAMPLE = "escritorio-exemplo.com.br"


async def seed_dev(factory: Factory, state: AppState) -> SeedResult:
    """Clients and users of the design-system sample data, for local development only.

    Passwords and TOTP secrets are generated at random and returned (the CLI prints them once);
    nothing secret is versioned. Refuses to run outside dev or when clients already exist.
    """
    settings = state.settings
    if settings.environment != "dev":
        raise AdminError("seed-dev só roda com REGISTA_ENVIRONMENT=dev.")

    result = SeedResult(users=[])
    async with tenant_session(factory, platform_admin=True) as db:
        existing: int = (
            await db.execute(text("SELECT count(*) FROM tenants WHERE NOT is_internal"))
        ).scalar_one()
        if existing:
            raise AdminError(
                "Já existem clientes neste banco; o seed só roda em banco vazio. "
                "Para recomeçar, recrie o banco de dev (veja a seção Comandos do CLAUDE.md)."
            )
        internal = await _internal_tenant(db)
        example = await _create_tenant(db, "Escritório Exemplo", "escritorio-exemplo", 2026, 8)
        demo = await _create_tenant(
            db, "Artemisys (demonstração)", "artemisys-demonstracao", 2026, 7
        )

        await _enter_tenant(db, internal)
        result.users.append(
            await _active_user(
                db,
                state,
                internal,
                "equipe@artemisys.example.com",
                "tenant_admin",
                "Artemisys (equipe)",
                mfa_on=True,
                platform_admin=True,
                display_name="Equipe Artemisys (dev)",
            )
        )

        await _enter_tenant(db, example)
        for local, role, mfa_on in (
            ("admin", "tenant_admin", True),
            ("operacao", "operator", True),
            ("financeiro", "viewer", False),  # password set, MFA not finished: "Sem MFA"
        ):
            result.users.append(
                await _active_user(
                    db,
                    state,
                    example,
                    f"{local}@{_EXAMPLE}",
                    role,
                    "Escritório Exemplo",
                    mfa_on=mfa_on,
                )
            )

        pending = f"estagio@{_EXAMPLE}"
        pending_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO users (tenant_id, email, role) VALUES (:t, :e, 'viewer')"
                    " RETURNING id"
                ),
                {"t": example, "e": pending},
            )
        ).scalar_one()
        token = await issue_invitation(
            db, tenant_id=example, user_id=pending_id, created_by=None, settings=settings
        )
        result.users.append(SeedUser(pending, "viewer", "Escritório Exemplo", invitation_sent=True))

        # The demonstration robot (bots/demo_busca_google) and the pool it runs in. The machine
        # is registered in the panel, so the dev sees the whole flow (docs/STATUS.md, M3).
        await _enter_tenant(db, demo)
        demo_pool: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO pools (tenant_id, name) VALUES (:t, 'Artemisys – Demonstração')"  # noqa: RUF001  (the design-system sample name)
                    " RETURNING id"
                ),
                {"t": demo},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO bots (tenant_id, pool_id, name, package_name, description)"
                " VALUES (:t, :p, 'Busca no Google', 'demo_busca_google',"
                " 'Pesquisa um termo no Google e tira uma captura de tela.')"
            ),
            {"t": demo, "p": demo_pool},
        )
        await _enter_tenant(db, example)
        await audit.record(
            db,
            tenant_id=example,
            actor_type="system",
            actor_id=None,
            action="seed.dev",
        )
    await send_invitation(state, to=pending, token=token)
    return result


async def _create_tenant(
    db: AsyncSession, name: str, slug: str, year: int, month: int
) -> uuid.UUID:
    created: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO tenants (name, slug, data_region, created_at)"
                " VALUES (:n, :s, :r, :c) RETURNING id"
            ),
            {
                "n": name,
                "s": slug,
                "r": DEFAULT_REGION,
                "c": datetime(year, month, 15, 12, 0, tzinfo=UTC),
            },
        )
    ).scalar_one()
    return created


async def _active_user(
    db: AsyncSession,
    state: AppState,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    client: str,
    *,
    mfa_on: bool,
    platform_admin: bool = False,
    display_name: str | None = None,
) -> SeedUser:
    password = secrets.token_urlsafe(18)
    user = SeedUser(email, role, client, password=password)
    password_hash = await asyncio.to_thread(hash_password, password)
    user_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO users (tenant_id, email, role, is_platform_admin, display_name,"
                " password_hash, status, last_login_at)"
                " VALUES (:t, :e, :r, :p, :d, :h, 'active', now()) RETURNING id"
            ),
            {
                "t": tenant_id,
                "e": email,
                "r": role,
                "p": platform_admin,
                "d": display_name,
                "h": password_hash,
            },
        )
    ).scalar_one()
    if mfa_on:
        secret = mfa.new_secret()
        ciphertext, key_id = mfa.encrypt_secret(state.keys, tenant_id, user_id, secret)
        await db.execute(
            text(
                "UPDATE users SET mfa_secret_enc = :c, mfa_key_id = :k, mfa_enabled = true,"
                " mfa_enabled_at = now() WHERE id = :id"
            ),
            {"c": ciphertext, "k": key_id, "id": user_id},
        )
        codes = [new_recovery_code() for _ in range(10)]
        hashes = await asyncio.to_thread(
            lambda: [hash_password(normalize_recovery_code(c)) for c in codes]
        )
        await db.execute(
            text("INSERT INTO recovery_codes (tenant_id, user_id, code_hash) VALUES (:t, :u, :h)"),
            [{"t": tenant_id, "u": user_id, "h": h} for h in hashes],
        )
        user.totp_secret = secret
        user.otpauth_uri = mfa.provisioning_uri(secret, email)
        user.recovery_codes = codes
    return user
