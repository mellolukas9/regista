"""Rate limiting without Redis (ADR 0003): fixed windows counted in PostgreSQL.

State lives in the database, so it is shared by every API instance. Keys are hashed, so no
e-mail address or IP is stored in the clear.
"""

import hashlib
from collections.abc import Iterable

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.config import Settings
from regista_api.core.db import tenant_session


def _key(scope: str, subject: str) -> bytes:
    return hashlib.sha256(f"{scope}:{subject.strip().lower()}".encode()).digest()


async def rate_limited(
    factory: async_sessionmaker[AsyncSession],
    *,
    scope: str,
    subject: str,
    window_seconds: int,
    max_hits: int,
) -> bool:
    """Count one hit and report whether `subject` is over the limit for `scope`.

    Runs in its own transaction on purpose: the hit must survive the request being rejected.
    """
    async with tenant_session(factory) as session:
        result = await session.execute(
            text("SELECT app.rate_limit_hit(:k, :w, :m)"),
            {"k": _key(scope, subject), "w": window_seconds, "m": max_hits},
        )
        return bool(result.scalar_one())


def client_ip(request: Request, settings: Settings) -> str | None:
    """Real client address. X-Forwarded-For is only believed when it comes from a trusted
    proxy, and is read right to left so values the client injected are ignored."""
    peer = request.client.host if request.client else None
    if peer is None or peer not in settings.trusted_proxy_set:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    return _first_untrusted(reversed(hops), settings.trusted_proxy_set) or peer


def _first_untrusted(hops: Iterable[str], trusted: frozenset[str]) -> str | None:
    for hop in hops:
        if hop not in trusted:
            return hop
    return None
