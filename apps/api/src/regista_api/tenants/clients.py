"""Clients (tenants): only the Artemisys team lists and creates them (design-system 7.16)."""

import re
import unicodedata
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.invitations import issue_invitation
from regista_api.auth.mail import send_invitation
from regista_api.auth.permissions import Permission
from regista_api.core.db import tenant_session
from regista_api.core.errors import api_error
from regista_api.core.pagination import Pagination, like_pattern, order_by
from regista_api.tenants.schemas import (
    ClientItem,
    ClientList,
    CreateClientRequest,
    CreateClientResponse,
)

router = APIRouter(prefix="/clients", tags=["clients"])

DEFAULT_REGION = "sa-east-1"

# ORDER BY expressions are fixed here; user input only picks a key (core/pagination.py).
_SORT = {"name": "lower(t.name)", "created_at": "t.created_at", "users": "users_count"}

_WHERE = "NOT t.is_internal AND (CAST(:like AS text) IS NULL OR t.name ILIKE :like ESCAPE '\\')"


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60].strip("-")
    return slug or "cliente"


@router.get("", response_model=ClientList)
async def list_clients(
    auth: Annotated[Auth, Depends(Require(Permission.CLIENTS_VIEW_ALL))],
    paging: Pagination,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> ClientList:
    ordering = order_by(sort, _SORT, "name")
    params = {"like": like_pattern(q)}
    # Not tied to the selected client: the list is the whole portfolio, internal tenant excluded.
    async with tenant_session(auth.state.factory, platform_admin=True) as db:
        total: int = (
            await db.execute(text(f"SELECT count(*) FROM tenants t WHERE {_WHERE}"), params)  # noqa: S608
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    "SELECT t.id, t.name, t.created_at,"  # noqa: S608
                    " (SELECT count(*) FROM users u"
                    "  WHERE u.tenant_id = t.id AND u.status <> 'disabled') AS users_count"
                    f" FROM tenants t WHERE {_WHERE} ORDER BY {ordering}"
                    " LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": paging.per_page, "offset": paging.offset},
            )
        ).all()
    return ClientList(
        items=[
            ClientItem(id=r.id, name=r.name, created_at=r.created_at, users_count=r.users_count)
            for r in rows
        ],
        total=total,
        page=paging.page,
        per_page=paging.per_page,
    )


@router.post("", response_model=CreateClientResponse, status_code=201)
async def create_client(
    body: CreateClientRequest,
    auth: Annotated[Auth, Depends(Require(Permission.CLIENTS_CREATE))],
) -> CreateClientResponse:
    """Client + its first Admin (invited) in one transaction, so a rejected e-mail leaves
    nothing behind."""
    name = " ".join(body.name.split())
    state = auth.state
    try:
        async with tenant_session(state.factory, platform_admin=True) as db:
            if (
                await db.execute(
                    text("SELECT 1 FROM tenants WHERE lower(name) = lower(:n)"), {"n": name}
                )
            ).first():
                raise api_error(409, "client_name_taken")

            base, slug, n = slugify(name), "", 1
            while True:
                slug = base if n == 1 else f"{base}-{n}"
                if not (
                    await db.execute(text("SELECT 1 FROM tenants WHERE slug = :s"), {"s": slug})
                ).first():
                    break
                n += 1

            tenant_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO tenants (name, slug, data_region)"
                        " VALUES (:n, :s, :r) RETURNING id"
                    ),
                    {"n": name, "s": slug, "r": DEFAULT_REGION},
                )
            ).scalar_one()
            # Same transaction, now writing inside the new tenant (RLS WITH CHECK).
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            admin_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO users (tenant_id, email, role)"
                        " VALUES (:t, :e, 'tenant_admin') RETURNING id"
                    ),
                    {"t": tenant_id, "e": body.admin_email},
                )
            ).scalar_one()
            token = await issue_invitation(
                db,
                tenant_id=tenant_id,
                user_id=admin_id,
                created_by=auth.user.id,
                settings=state.settings,
            )
            for action, target_type, target_id in (
                ("tenant.created", "tenant", tenant_id),
                ("user.invited", "user", admin_id),
            ):
                await audit.record(
                    db,
                    tenant_id=tenant_id,
                    actor_type="user",
                    actor_id=auth.user.id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    metadata={"role": "tenant_admin"} if target_type == "user" else {},
                    ip=auth.ip,
                )
    except IntegrityError as exc:
        if "uq_users_email" in str(exc.orig):
            raise api_error(409, "email_in_use") from None
        raise
    await send_invitation(state, to=body.admin_email, token=token)
    return CreateClientResponse(id=tenant_id, name=name, admin_email=body.admin_email)
