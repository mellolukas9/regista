"""Pools and machines for the panel (docs/specs/design-system.md 7.13 and 7.14).

Reads follow the client context (`auth.scoped()`); every write goes through `auth.writing()`,
which refuses "all clients". Isolation between clients comes from RLS: an id from another client
is simply not found (404). The hidden internal tenant never shows up in any list.

The enrollment key is returned only by the two calls that create it, with `Cache-Control:
no-store`. What the agent itself sends (enroll, token, heartbeat) lives in `machines/agent.py`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.permissions import Permission
from regista_api.core.errors import api_error
from regista_api.core.pagination import Pagination, order_by
from regista_api.machines import service
from regista_api.machines.schemas import (
    ClientNoSignal,
    CreateMachineRequest,
    CreatePoolRequest,
    CurrentJob,
    IssuedKey,
    MachineDetail,
    MachineEvent,
    MachineEventList,
    MachineItem,
    MachineList,
    MachinesSummary,
    PoolItem,
    PoolList,
    RevokeMachineRequest,
)

router = APIRouter(tags=["machines"])

_VIEW = Depends(Require(Permission.MACHINES_VIEW))
_MANAGE = Depends(Require(Permission.MACHINES_MANAGE))

# ORDER BY expressions are fixed here; user input only picks a key (core/pagination.py).
_SORT = {
    "name": "lower(m.name)",
    "status": "m.status",
    "pool": "lower(p.name)",
    "last_seen_at": "m.last_seen_at",
    "created_at": "m.created_at",
}

_FROM = (
    "FROM machines m"
    " JOIN pools p ON p.tenant_id = m.tenant_id AND p.id = m.pool_id"
    " JOIN tenants t ON t.id = m.tenant_id"
    " LEFT JOIN LATERAL (SELECT k.expires_at, k.created_at FROM enrollment_keys k"
    "   WHERE k.machine_id = m.id AND k.used_at IS NULL AND k.revoked_at IS NULL"
    "   LIMIT 1) live ON true"
    " LEFT JOIN LATERAL (SELECT j.id, j.short_code, j.status FROM jobs j"
    "   WHERE j.tenant_id = m.tenant_id AND j.machine_id = m.id"
    "   AND j.status IN ('assigned', 'running') LIMIT 1) cur ON true"
)
_ITEM_COLUMNS = (
    "m.id, m.name, m.status, m.mode, m.pool_id, p.name AS pool_name, m.tenant_id,"
    " t.name AS client_name, m.last_seen_at, m.agent_version, m.created_at,"
    " live.expires_at AS key_expires_at, live.created_at AS key_created_at, m.paused_locally,"
    " cur.id AS cur_id, cur.short_code AS cur_code, cur.status AS cur_status"
)
# Staff (internal tenant) are not visible under a client's RLS, so a machine they registered is
# shown as theirs whichever way the row is read.
_DETAIL_COLUMNS = (
    f"{_ITEM_COLUMNS}, m.os_info, m.max_concurrency, m.enrolled_at, m.revoked_at,"
    " CASE WHEN m.created_by IS NULL THEN NULL"
    "      WHEN cu.tenant_id = m.tenant_id THEN cu.email::text"
    "      ELSE 'Equipe Artemisys' END AS created_by_label"
)
_DETAIL_FROM = f"{_FROM} LEFT JOIN users cu ON cu.id = m.created_by"
_OS_INFO_KEYS = ("system", "release", "hostname", "python")


# --- pools ------------------------------------------------------------------------------------


@router.get("/pools", response_model=PoolList)
async def list_pools(auth: Annotated[Auth, _VIEW]) -> PoolList:
    async with auth.scoped() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT p.id, p.name, p.kind, p.tenant_id, t.name AS client_name,"
                    " p.created_at,"
                    " count(m.id) FILTER (WHERE m.status <> 'revoked') AS machines_total,"
                    " count(m.id) FILTER (WHERE m.status = 'online') AS machines_online,"
                    " (SELECT coalesce(array_agg(b.name ORDER BY lower(b.name)), '{}')"
                    "  FROM bots b WHERE b.tenant_id = p.tenant_id AND b.pool_id = p.id"
                    "  AND b.is_active) AS bot_names"
                    " FROM pools p JOIN tenants t ON t.id = p.tenant_id"
                    " LEFT JOIN machines m ON m.tenant_id = p.tenant_id AND m.pool_id = p.id"
                    " WHERE NOT t.is_internal"
                    " GROUP BY p.id, t.name ORDER BY lower(p.name), p.id"
                )
            )
        ).all()
    return PoolList(items=[_pool_item(r) for r in rows])


@router.post("/pools", response_model=PoolItem, status_code=201)
async def create_pool(body: CreatePoolRequest, auth: Annotated[Auth, _MANAGE]) -> PoolItem:
    name = body.name.strip()
    if not name:
        raise api_error(422, "invalid_name")
    try:
        async with auth.writing() as db:
            row = (
                await db.execute(
                    text(
                        "INSERT INTO pools (tenant_id, name, created_by)"
                        " VALUES (:t, :n, :u) RETURNING id, name, kind, tenant_id, created_at"
                    ),
                    {"t": auth.client_id, "n": name, "u": auth.user.id},
                )
            ).one()
            await audit.record(
                db,
                tenant_id=auth.client_id,
                actor_type="user",
                actor_id=auth.user.id,
                action="pool.created",
                target_type="pool",
                target_id=row.id,
                metadata={"name": name},
                ip=auth.ip,
            )
    except IntegrityError as exc:
        if "uq_pools_tenant_name" in str(exc.orig):
            raise api_error(409, "pool_name_taken") from None
        raise
    return PoolItem(
        id=row.id,
        name=row.name,
        kind=row.kind,
        client_id=row.tenant_id,
        client_name=auth.scope.name or "",
        machines_total=0,
        machines_online=0,
        created_at=row.created_at,
    )


# --- machines ---------------------------------------------------------------------------------


@router.get("/machines", response_model=MachineList)
async def list_machines(
    auth: Annotated[Auth, _VIEW],
    paging: Pagination,
    pool_id: Annotated[uuid.UUID | None, Query()] = None,
    include_revoked: Annotated[bool, Query()] = False,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> MachineList:
    ordering = order_by(sort, _SORT, "name")
    scope = "NOT t.is_internal AND (CAST(:pool_id AS uuid) IS NULL OR m.pool_id = :pool_id)"
    params: dict[str, Any] = {"pool_id": pool_id}
    async with auth.scoped() as db:
        visible = f"{scope} AND (m.status <> 'revoked' OR :include_revoked)"
        total: int = (
            await db.execute(
                text(f"SELECT count(*) {_FROM} WHERE {visible}"),
                {**params, "include_revoked": include_revoked},
            )
        ).scalar_one()
        revoked_count: int = (
            await db.execute(
                text(f"SELECT count(*) {_FROM} WHERE {scope} AND m.status = 'revoked'"),
                params,
            )
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    f"SELECT {_ITEM_COLUMNS} {_FROM} WHERE {visible}"
                    f" ORDER BY {ordering} LIMIT :limit OFFSET :offset"
                ),
                {
                    **params,
                    "include_revoked": include_revoked,
                    "limit": paging.per_page,
                    "offset": paging.offset,
                },
            )
        ).all()
    return MachineList(
        items=[_machine_item(r) for r in rows],
        total=total,
        page=paging.page,
        per_page=paging.per_page,
        revoked_count=revoked_count,
    )


@router.post("/machines", response_model=IssuedKey, status_code=201)
async def create_machine(
    body: CreateMachineRequest, auth: Annotated[Auth, _MANAGE], response: Response
) -> IssuedKey:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with auth.writing() as db:
            pool = (
                await db.execute(text("SELECT id FROM pools WHERE id = :p"), {"p": body.pool_id})
            ).first()
            if pool is None:
                raise api_error(404, "pool_not_found")
            machine_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO machines (tenant_id, pool_id, name, mode, created_by)"
                        " VALUES (:t, :p, :n, :m, :u) RETURNING id"
                    ),
                    {
                        "t": auth.client_id,
                        "p": body.pool_id,
                        "n": body.name,
                        "m": body.mode,
                        "u": auth.user.id,
                    },
                )
            ).scalar_one()
            key, expires_at = await service.issue_enrollment_key(
                db,
                tenant_id=auth.client_id,
                machine_id=machine_id,
                created_by=auth.user.id,
                settings=auth.state.settings,
            )
            await _audit(
                db,
                auth,
                "machine.created",
                machine_id,
                {"pool_id": str(body.pool_id), "mode": body.mode},
            )
    except IntegrityError as exc:
        if "uq_machines_tenant_name" in str(exc.orig):
            raise api_error(409, "machine_name_taken") from None
        raise
    return IssuedKey(
        machine_id=machine_id,
        name=body.name,
        enrollment_key=key,
        expires_at=expires_at,
        server_url=service.server_url(auth.state.settings),
    )


# Declared before `/machines/{machine_id}` so "summary" is never read as an id.
@router.get("/machines/summary", response_model=MachinesSummary)
async def machines_summary(auth: Annotated[Auth, _VIEW]) -> MachinesSummary:
    async with auth.scoped() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT m.tenant_id, t.name AS client_name,"
                    " count(*) FILTER (WHERE m.status <> 'revoked') AS total,"
                    " count(*) FILTER (WHERE m.status = 'online') AS online,"
                    " count(*) FILTER (WHERE m.status = 'offline') AS no_signal"
                    " FROM machines m JOIN tenants t ON t.id = m.tenant_id"
                    " WHERE NOT t.is_internal GROUP BY m.tenant_id, t.name"
                )
            )
        ).all()
    clients = (
        sorted(
            (
                ClientNoSignal(
                    client_id=r.tenant_id, client_name=r.client_name, no_signal=r.no_signal
                )
                for r in rows
                if r.no_signal > 0
            ),
            key=lambda c: c.client_name.lower(),
        )
        if auth.scope.all_clients
        else []
    )
    return MachinesSummary(
        total=sum(r.total for r in rows),
        online=sum(r.online for r in rows),
        no_signal=sum(r.no_signal for r in rows),
        clients=clients,
    )


@router.get("/machines/{machine_id}", response_model=MachineDetail)
async def get_machine(machine_id: uuid.UUID, auth: Annotated[Auth, _VIEW]) -> MachineDetail:
    async with auth.scoped() as db:
        return await _detail(db, machine_id)


@router.get("/machines/{machine_id}/events", response_model=MachineEventList)
async def machine_events(
    machine_id: uuid.UUID, auth: Annotated[Auth, _VIEW], paging: Pagination
) -> MachineEventList:
    async with auth.scoped() as db:
        found = (
            await db.execute(text("SELECT 1 FROM machines WHERE id = :m"), {"m": machine_id})
        ).first()
        if found is None:
            raise api_error(404, "machine_not_found")
        total: int = (
            await db.execute(
                text("SELECT count(*) FROM machine_events WHERE machine_id = :m"),
                {"m": machine_id},
            )
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    "SELECT id, kind, created_at, metadata FROM machine_events"
                    " WHERE machine_id = :m ORDER BY created_at DESC, id DESC"
                    " LIMIT :limit OFFSET :offset"
                ),
                {"m": machine_id, "limit": paging.per_page, "offset": paging.offset},
            )
        ).all()
    return MachineEventList(
        items=[
            MachineEvent(id=r.id, kind=r.kind, created_at=r.created_at, metadata=r.metadata)
            for r in rows
        ],
        total=total,
        page=paging.page,
        per_page=paging.per_page,
    )


@router.post("/machines/{machine_id}/enrollment-key", response_model=IssuedKey, status_code=201)
async def new_enrollment_key(
    machine_id: uuid.UUID, auth: Annotated[Auth, _MANAGE], response: Response
) -> IssuedKey:
    """A new key for any machine that is not revoked. Nothing changes on a machine that is
    already enrolled until the key is used (then the agent installed today stops working)."""
    response.headers["Cache-Control"] = "no-store"
    async with auth.writing() as db:
        target = await _target(db, machine_id)
        if target.status == "revoked":
            raise api_error(409, "machine_revoked")
        key, expires_at = await service.issue_enrollment_key(
            db,
            tenant_id=auth.client_id,
            machine_id=machine_id,
            created_by=auth.user.id,
            settings=auth.state.settings,
        )
        await _audit(
            db,
            auth,
            "machine.key_generated",
            machine_id,
            {"replaces_agent": target.status != "pending", "expires_at": expires_at.isoformat()},
        )
    return IssuedKey(
        machine_id=machine_id,
        name=target.name,
        enrollment_key=key,
        expires_at=expires_at,
        server_url=service.server_url(auth.state.settings),
    )


@router.post("/machines/{machine_id}/revoke", response_model=MachineDetail)
async def revoke_machine(
    machine_id: uuid.UUID, body: RevokeMachineRequest, auth: Annotated[Auth, _MANAGE]
) -> MachineDetail:
    """Immediate: the next request of the agent is refused (`machine_revoked`). Revoked machines
    cannot be reused; to bring the computer back, register it again as a new machine."""
    async with auth.writing() as db:
        target = await _target(db, machine_id)
        if body.confirm_name != target.name:
            raise api_error(422, "name_mismatch")
        if target.status != "revoked":
            await db.execute(
                text(
                    "UPDATE machines SET status = 'revoked', revoked_at = now(),"
                    " revoked_by = :u, challenge_hash = NULL, challenge_expires_at = NULL,"
                    " updated_at = now() WHERE id = :m"
                ),
                {"u": auth.user.id, "m": machine_id},
            )
            await db.execute(
                text(
                    "UPDATE enrollment_keys SET revoked_at = now()"
                    " WHERE machine_id = :m AND used_at IS NULL AND revoked_at IS NULL"
                ),
                {"m": machine_id},
            )
            # The run on this machine ends with it. The agent finds out on its next call (the
            # token is refused) and stops the robot.
            cancelled = await db.execute(
                text(
                    "UPDATE jobs SET status = 'cancelled', error_code = 'machine_revoked',"
                    " cancel_requested_at = coalesce(cancel_requested_at, now()),"
                    " finished_at = now(), updated_at = now()"
                    " WHERE machine_id = :m AND status IN ('assigned', 'running') RETURNING id"
                ),
                {"m": machine_id},
            )
            for job in cancelled.all():
                await _audit_job(db, auth, job.id, machine_id)
            await service.record_event(
                db,
                tenant_id=auth.client_id,
                machine_id=machine_id,
                kind="revoked",
                metadata={"by": str(auth.user.id)},
            )
            await _audit(db, auth, "machine.revoked", machine_id, {"name": target.name})
        return await _detail(db, machine_id)


# --- helpers ----------------------------------------------------------------------------------


async def _target(db: AsyncSession, machine_id: uuid.UUID) -> Row[Any]:
    """The machine in the current client (RLS hides everyone else), locked for the change."""
    row = (
        await db.execute(
            text("SELECT id, name, status FROM machines WHERE id = :id FOR UPDATE"),
            {"id": machine_id},
        )
    ).first()
    if row is None:
        raise api_error(404, "machine_not_found")
    return row


async def _detail(db: AsyncSession, machine_id: uuid.UUID) -> MachineDetail:
    row = (
        await db.execute(
            text(f"SELECT {_DETAIL_COLUMNS} {_DETAIL_FROM} WHERE m.id = :id AND NOT t.is_internal"),
            {"id": machine_id},
        )
    ).first()
    if row is None:
        raise api_error(404, "machine_not_found")
    os_info = row.os_info if isinstance(row.os_info, dict) else {}
    return MachineDetail(
        **_machine_item(row).model_dump(),
        # Agent-supplied: only the known keys, as short strings.
        os_info={k: str(os_info[k])[:100] for k in _OS_INFO_KEYS if k in os_info},
        max_concurrency=row.max_concurrency,
        enrolled_at=row.enrolled_at,
        created_by=row.created_by_label,
        revoked_at=row.revoked_at,
    )


def _machine_item(r: Row[Any]) -> MachineItem:
    return MachineItem(
        id=r.id,
        name=r.name,
        status=r.status,
        mode=r.mode,
        pool_id=r.pool_id,
        pool_name=r.pool_name,
        client_id=r.tenant_id,
        client_name=r.client_name,
        last_seen_at=r.last_seen_at,
        agent_version=r.agent_version,
        key_expires_at=r.key_expires_at,
        key_created_at=r.key_created_at,
        created_at=r.created_at,
        paused_locally=bool(r.paused_locally) and r.status == "online",
        current_job=None
        if r.cur_id is None
        else CurrentJob(id=r.cur_id, short_code=r.cur_code, status=r.cur_status),
    )


def _pool_item(r: Row[Any]) -> PoolItem:
    return PoolItem(
        id=r.id,
        name=r.name,
        kind=r.kind,
        client_id=r.tenant_id,
        client_name=r.client_name,
        machines_total=r.machines_total,
        machines_online=r.machines_online,
        created_at=r.created_at,
        bot_names=list(getattr(r, "bot_names", None) or []),
    )


async def _audit_job(
    db: AsyncSession, auth: Auth, job_id: uuid.UUID, machine_id: uuid.UUID
) -> None:
    await audit.record(
        db,
        tenant_id=auth.client_id,
        actor_type="user",
        actor_id=auth.user.id,
        action="job.cancelled",
        target_type="job",
        target_id=job_id,
        metadata={"because": "machine_revoked", "machine_id": str(machine_id)},
        ip=auth.ip,
    )


async def _audit(
    db: AsyncSession, auth: Auth, action: str, machine_id: uuid.UUID, metadata: dict[str, object]
) -> None:
    await audit.record(
        db,
        tenant_id=auth.client_id,
        actor_type="user",
        actor_id=auth.user.id,
        action=action,
        target_type="machine",
        target_id=machine_id,
        metadata=metadata,
        ip=auth.ip,
    )
