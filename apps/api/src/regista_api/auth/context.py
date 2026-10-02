"""Which client a request works on (docs/specs/security.md, design-system section 4).

- Client users: always their own client. The `rg_client` cookie is ignored.
- Platform admins: the client in the `rg_client` cookie, validated here on every request
  (it must exist, be active and not be the hidden internal tenant). No valid cookie means
  "all clients": consolidated, read-only.
"""

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.sessions import CLIENT_COOKIE
from regista_api.core.db import tenant_session


@dataclass(frozen=True)
class ClientScope:
    """`tenant_id` is None for "all clients"."""

    tenant_id: uuid.UUID | None
    name: str | None

    @property
    def all_clients(self) -> bool:
        return self.tenant_id is None


ALL_CLIENTS = ClientScope(None, None)


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw or len(raw) > 64:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def selectable_client(
    factory: async_sessionmaker[AsyncSession], client_id: uuid.UUID
) -> ClientScope | None:
    """The client, if a platform admin may work on it (exists, active, not internal)."""
    async with tenant_session(factory, platform_admin=True) as db:
        row = (
            await db.execute(
                text(
                    "SELECT id, name FROM tenants WHERE id = :i AND is_active AND NOT is_internal"
                ),
                {"i": client_id},
            )
        ).first()
    return ClientScope(row.id, row.name) if row else None


async def resolve_scope(
    request: Request,
    factory: async_sessionmaker[AsyncSession],
    *,
    is_platform_admin: bool,
    own_tenant_id: uuid.UUID,
    own_tenant_name: str,
) -> ClientScope:
    if not is_platform_admin:
        return ClientScope(own_tenant_id, own_tenant_name)
    client_id = _parse_uuid(request.cookies.get(CLIENT_COOKIE))
    if client_id is None:
        return ALL_CLIENTS
    return await selectable_client(factory, client_id) or ALL_CLIENTS
