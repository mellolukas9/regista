"""Bots for the panel (docs/specs/design-system.md 7.5 and 7.6).

Reads follow the client context (`auth.scoped()`); registering a bot is Artemisys-only and goes
through `auth.writing()`, which refuses "all clients". An id from another client is simply not
found (404). Versions, schedules and the queue of a bot arrive with M4, M7 and M5.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.permissions import Permission
from regista_api.bots.schemas import (
    BotDetail,
    BotItem,
    BotList,
    CreateBotRequest,
    LastRun,
)
from regista_api.bots.version_schemas import CurrentVersion
from regista_api.core.errors import api_error
from regista_api.core.pagination import Pagination, like_pattern, order_by

router = APIRouter(tags=["bots"])

_VIEW = Depends(Require(Permission.BOTS_VIEW))
_MANAGE = Depends(Require(Permission.BOTS_MANAGE))

_SORT = {
    "name": "lower(b.name)",
    "created_at": "b.created_at",
    "pool": "lower(p.name)",
}

_FROM = (
    "FROM bots b"
    " JOIN pools p ON p.tenant_id = b.tenant_id AND p.id = b.pool_id"
    " JOIN tenants t ON t.id = b.tenant_id"
    " LEFT JOIN bot_versions cv ON cv.tenant_id = b.tenant_id AND cv.bot_id = b.id"
    " AND cv.id = b.current_version_id"
)
_COLUMNS = (
    "b.id, b.name, b.package_name, b.description, b.pool_id, p.name AS pool_name,"
    " b.tenant_id, t.name AS client_name, b.is_active, b.created_at,"
    " cv.id AS current_version_id, cv.version AS current_version,"
    " EXISTS (SELECT 1 FROM jobs a WHERE a.tenant_id = b.tenant_id AND a.bot_id = b.id"
    "         AND a.status IN ('pending', 'assigned', 'running')) AS has_active_run"
)
# The last 10 runs of each listed bot, newest first (flipped in Python).
_RECENT = text(
    "SELECT bot_id, id, short_code, status, created_at, finished_at FROM ("
    " SELECT j.bot_id, j.id, j.short_code, j.status, j.created_at, j.finished_at,"
    "  row_number() OVER (PARTITION BY j.bot_id ORDER BY j.created_at DESC, j.id DESC) AS rn"
    " FROM jobs j WHERE j.bot_id = ANY(:ids)) x"
    " WHERE rn <= 10 ORDER BY bot_id, rn"
)


async def _items(db: AsyncSession, rows: list[Row[Any]]) -> list[BotItem]:
    recent: dict[uuid.UUID, list[Row[Any]]] = {}
    if rows:
        for r in (await db.execute(_RECENT, {"ids": [r.id for r in rows]})).all():
            recent.setdefault(r.bot_id, []).append(r)
    items: list[BotItem] = []
    for r in rows:
        runs = recent.get(r.id, [])
        last = runs[0] if runs else None
        items.append(
            BotItem(
                id=r.id,
                name=r.name,
                package_name=r.package_name,
                description=r.description,
                pool_id=r.pool_id,
                pool_name=r.pool_name,
                client_id=r.tenant_id,
                client_name=r.client_name,
                is_active=r.is_active,
                created_at=r.created_at,
                last_run=None
                if last is None
                else LastRun(
                    id=last.id,
                    short_code=last.short_code,
                    status=last.status,
                    created_at=last.created_at,
                    finished_at=last.finished_at,
                ),
                recent_statuses=[x.status for x in reversed(runs)],
                recent_ids=[x.id for x in reversed(runs)],
                has_active_run=r.has_active_run,
                current_version=None
                if r.current_version_id is None
                else CurrentVersion(id=r.current_version_id, version=r.current_version),
            )
        )
    return items


@router.get("/bots", response_model=BotList)
async def list_bots(
    auth: Annotated[Auth, _VIEW],
    paging: Pagination,
    q: Annotated[str | None, Query(max_length=80)] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> BotList:
    ordering = order_by(sort, _SORT, "name").replace(", id ASC", ", b.id ASC")
    where = "NOT t.is_internal AND (CAST(:q AS text) IS NULL OR b.name ILIKE :q ESCAPE '\\')"
    params: dict[str, Any] = {"q": like_pattern(q)}
    async with auth.scoped() as db:
        total: int = (
            await db.execute(text(f"SELECT count(*) {_FROM} WHERE {where}"), params)
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    f"SELECT {_COLUMNS} {_FROM} WHERE {where}"
                    f" ORDER BY {ordering} LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": paging.per_page, "offset": paging.offset},
            )
        ).all()
        items = await _items(db, list(rows))
    return BotList(items=items, total=total, page=paging.page, per_page=paging.per_page)


@router.post("/bots", response_model=BotItem, status_code=201)
async def create_bot(body: CreateBotRequest, auth: Annotated[Auth, _MANAGE]) -> BotItem:
    name = body.name.strip()
    if not name:
        raise api_error(422, "invalid_name")
    try:
        async with auth.writing() as db:
            pool = (
                await db.execute(text("SELECT id FROM pools WHERE id = :p"), {"p": body.pool_id})
            ).first()
            if pool is None:
                raise api_error(404, "pool_not_found")
            bot_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO bots (tenant_id, pool_id, name, package_name, description,"
                        " created_by) VALUES (:t, :p, :n, :k, :d, :u) RETURNING id"
                    ),
                    {
                        "t": auth.client_id,
                        "p": body.pool_id,
                        "n": name,
                        "k": body.package_name,
                        "d": (body.description or "").strip() or None,
                        "u": auth.user.id,
                    },
                )
            ).scalar_one()
            await audit.record(
                db,
                tenant_id=auth.client_id,
                actor_type="user",
                actor_id=auth.user.id,
                action="bot.created",
                target_type="bot",
                target_id=bot_id,
                metadata={"name": name, "package_name": body.package_name},
                ip=auth.ip,
            )
            row = (
                await db.execute(
                    text(f"SELECT {_COLUMNS} {_FROM} WHERE b.id = :id"), {"id": bot_id}
                )
            ).one()
            return (await _items(db, [row]))[0]
    except IntegrityError as exc:
        reason = str(exc.orig)
        if "uq_bots_tenant_name" in reason:
            raise api_error(409, "bot_name_taken") from None
        if "uq_bots_tenant_package" in reason:
            raise api_error(409, "package_name_taken") from None
        raise


@router.get("/bots/{bot_id}", response_model=BotDetail)
async def get_bot(bot_id: uuid.UUID, auth: Annotated[Auth, _VIEW]) -> BotDetail:
    async with auth.scoped() as db:
        row = (
            await db.execute(
                text(f"SELECT {_COLUMNS} {_FROM} WHERE b.id = :id AND NOT t.is_internal"),
                {"id": bot_id},
            )
        ).first()
        if row is None:
            raise api_error(404, "bot_not_found")
        item = (await _items(db, [row]))[0]
        pool = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE status <> 'revoked') AS total,"
                    " count(*) FILTER (WHERE status = 'online') AS online"
                    " FROM machines WHERE tenant_id = :t AND pool_id = :p"
                ),
                {"t": row.tenant_id, "p": row.pool_id},
            )
        ).one()
        stats = (
            await db.execute(
                text(
                    "SELECT count(*) AS finished, count(*) FILTER (WHERE status = 'completed')"
                    " AS ok FROM jobs WHERE tenant_id = :t AND bot_id = :b"
                    " AND status IN ('completed', 'failed')"
                    " AND finished_at > now() - interval '30 days'"
                ),
                {"t": row.tenant_id, "b": bot_id},
            )
        ).one()
    return BotDetail(
        **item.model_dump(),
        machines_total=pool.total,
        machines_online=pool.online,
        runs_30d=stats.finished,
        success_rate_30d=None if stats.finished == 0 else stats.ok / stats.finished,
    )
