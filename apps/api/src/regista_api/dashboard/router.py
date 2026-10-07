"""The home screen's numbers (docs/specs/design-system.md 7.2), from runs and machines.

In M3 there are no items yet, so everything here comes from runs ("execuções") and machines;
item counts and "Próxima" arrive with M5 and M7. One call, read in the current client context
(`auth.scoped()`): a client user sees their client, a platform admin in "all clients" sees the
consolidated version. The hidden internal client never counts anywhere.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from regista_api.auth.deps import Auth, Require
from regista_api.auth.permissions import Permission
from regista_api.jobs.router import _COLUMNS, _FROM, job_item
from regista_api.jobs.schemas import JobItem
from regista_api.machines.schemas import CurrentJob

router = APIRouter(tags=["dashboard"])

DashPeriod = Literal["today", "7d", "30d"]

_VIEW = Depends(Require(Permission.JOBS_VIEW))
_BRT = "America/Sao_Paulo"
_SINCE = {
    "today": f"date_trunc('day', now() AT TIME ZONE '{_BRT}') AT TIME ZONE '{_BRT}'",
    "7d": "now() - interval '7 days'",
    "30d": "now() - interval '30 days'",
}
# A pending run that waits this long is something a person should look at.
_STUCK_MINUTES = 10


class DayCount(BaseModel):
    day: date
    completed: int
    failed: int


class AttentionItem(BaseModel):
    kind: Literal["machine_offline", "job_pending"]
    id: str
    label: str
    since: datetime | None
    client_name: str


class ClientRow(BaseModel):
    client_id: str
    client_name: str
    runs: int
    success_rate: float | None
    machines_total: int
    machines_online: int
    machines_no_signal: int
    last_run_at: datetime | None


class MachineRow(BaseModel):
    id: str
    name: str
    status: str
    last_seen_at: datetime | None
    current_job: CurrentJob | None


class Silent(BaseModel):
    id: str
    name: str
    mode: str
    last_seen_at: datetime | None
    # Another machine of the same pool that is online and takes over, if any.
    other_online: str | None


class Dashboard(BaseModel):
    all_clients: bool
    period: str
    generated_at: datetime
    clients_active: int | None
    runs_total: int
    runs_running: int
    runs_pending: int
    runs_finished: int
    success_rate: float | None
    machines_total: int
    machines_online: int
    machines_no_signal: int
    daily: list[DayCount]
    attention: list[AttentionItem]
    by_client: list[ClientRow]
    latest_runs: list[JobItem]
    machines: list[MachineRow]
    pending_runs: list[JobItem]
    silent: list[Silent]


@router.get("/dashboard", response_model=Dashboard)
async def dashboard(
    auth: Annotated[Auth, _VIEW], period: Annotated[DashPeriod, Query()] = "7d"
) -> Dashboard:
    all_clients = auth.scope.all_clients
    since = _SINCE[period]
    async with auth.scoped() as db:
        clients_active: int | None = None
        if all_clients:
            clients_active = (
                await db.execute(
                    text("SELECT count(*) FROM tenants WHERE NOT is_internal AND is_active")
                )
            ).scalar_one()

        runs = (
            await db.execute(
                text(
                    "SELECT count(*) AS total,"
                    " count(*) FILTER (WHERE j.status = 'running') AS running,"
                    " count(*) FILTER (WHERE j.status = 'pending') AS pending,"
                    " count(*) FILTER (WHERE j.status IN ('completed', 'failed')) AS finished,"
                    " count(*) FILTER (WHERE j.status = 'completed') AS ok"
                    f" FROM jobs j JOIN tenants t ON t.id = j.tenant_id"
                    f" WHERE NOT t.is_internal AND j.created_at >= {since}"
                )
            )
        ).one()
        # "Executando" and "Pendente" are about now, not about the period.
        live = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE j.status IN ('running', 'assigned')) AS running,"
                    " count(*) FILTER (WHERE j.status = 'pending') AS pending"
                    " FROM jobs j JOIN tenants t ON t.id = j.tenant_id WHERE NOT t.is_internal"
                )
            )
        ).one()

        machines = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE m.status <> 'revoked') AS total,"
                    " count(*) FILTER (WHERE m.status = 'online') AS online,"
                    " count(*) FILTER (WHERE m.status = 'offline') AS no_signal"
                    " FROM machines m JOIN tenants t ON t.id = m.tenant_id WHERE NOT t.is_internal"
                )
            )
        ).one()

        daily = [
            DayCount(day=r.day, completed=r.completed, failed=r.failed)
            for r in await db.execute(
                text(
                    "WITH days AS (SELECT d::date AS day FROM generate_series("
                    f" (now() AT TIME ZONE '{_BRT}')::date - 13,"
                    f" (now() AT TIME ZONE '{_BRT}')::date,"
                    " interval '1 day') d)"
                    " SELECT days.day,"
                    " count(j.id) FILTER (WHERE j.status = 'completed') AS completed,"
                    " count(j.id) FILTER (WHERE j.status = 'failed') AS failed"
                    " FROM days LEFT JOIN ("
                    "   SELECT j.id, j.status, j.finished_at FROM jobs j"
                    "   JOIN tenants t ON t.id = j.tenant_id"
                    "   WHERE NOT t.is_internal AND j.status IN ('completed', 'failed')"
                    f"   AND j.finished_at >= now() - interval '15 days') j"
                    f" ON (j.finished_at AT TIME ZONE '{_BRT}')::date = days.day"
                    " GROUP BY days.day ORDER BY days.day"
                )
            )
        ]

        attention = await _attention(db)
        latest = (
            await db.execute(
                text(
                    f"SELECT {_COLUMNS} {_FROM} WHERE NOT t.is_internal"
                    " ORDER BY j.created_at DESC, j.id LIMIT 5"
                )
            )
        ).all()

        by_client: list[ClientRow] = []
        machine_rows: list[MachineRow] = []
        pending_rows: Sequence[Any] = []
        silent: list[Silent] = []
        if all_clients:
            by_client = await _by_client(db, since)
        else:
            machine_rows, silent = await _client_machines(db)
            pending_rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} {_FROM} WHERE NOT t.is_internal"
                        " AND j.status = 'pending' ORDER BY j.created_at LIMIT 5"
                    )
                )
            ).all()

    finished: int = runs.finished
    return Dashboard(
        all_clients=all_clients,
        period=period,
        generated_at=datetime.now(UTC),
        clients_active=clients_active,
        runs_total=runs.total,
        runs_running=live.running,
        runs_pending=live.pending,
        runs_finished=finished,
        success_rate=None if finished == 0 else runs.ok / finished,
        machines_total=machines.total,
        machines_online=machines.online,
        machines_no_signal=machines.no_signal,
        daily=daily,
        attention=attention,
        by_client=by_client,
        latest_runs=[job_item(r) for r in latest],
        machines=machine_rows,
        pending_runs=[job_item(r) for r in pending_rows],
        silent=silent,
    )


async def _attention(db: Any) -> list[AttentionItem]:
    items: list[AttentionItem] = []
    offline = await db.execute(
        text(
            "SELECT m.id, m.name, m.last_seen_at, t.name AS client FROM machines m"
            " JOIN tenants t ON t.id = m.tenant_id"
            " WHERE NOT t.is_internal AND m.status = 'offline' ORDER BY m.last_seen_at LIMIT 5"
        )
    )
    for r in offline:
        items.append(
            AttentionItem(
                kind="machine_offline",
                id=str(r.id),
                label=r.name,
                since=r.last_seen_at,
                client_name=r.client,
            )
        )
    stuck = await db.execute(
        text(
            "SELECT j.id, j.short_code, j.created_at, t.name AS client FROM jobs j"
            " JOIN tenants t ON t.id = j.tenant_id"
            " WHERE NOT t.is_internal AND j.status = 'pending'"
            f" AND j.created_at < now() - interval '{_STUCK_MINUTES} minutes'"
            " ORDER BY j.created_at LIMIT 5"
        )
    )
    for r in stuck:
        items.append(
            AttentionItem(
                kind="job_pending",
                id=str(r.id),
                label=r.short_code,
                since=r.created_at,
                client_name=r.client,
            )
        )
    # The five that have waited longest, whichever kind: machines must not crowd out stuck runs.
    items.sort(key=lambda i: (i.since is None, i.since))
    return items[:5]


async def _by_client(db: Any, since: str) -> list[ClientRow]:
    rows = await db.execute(
        text(
            "SELECT t.id, t.name,"
            " (SELECT count(*) FROM jobs j WHERE j.tenant_id = t.id"
            f"    AND j.created_at >= {since}) AS runs,"
            " (SELECT count(*) FROM jobs j WHERE j.tenant_id = t.id"
            f"    AND j.created_at >= {since} AND j.status = 'completed') AS ok,"
            " (SELECT count(*) FROM jobs j WHERE j.tenant_id = t.id"
            f"    AND j.created_at >= {since} AND j.status IN ('completed', 'failed')) AS done,"
            " (SELECT count(*) FROM machines m WHERE m.tenant_id = t.id"
            "    AND m.status <> 'revoked') AS m_total,"
            " (SELECT count(*) FROM machines m WHERE m.tenant_id = t.id"
            "    AND m.status = 'online') AS m_online,"
            " (SELECT count(*) FROM machines m WHERE m.tenant_id = t.id"
            "    AND m.status = 'offline') AS m_silent,"
            " (SELECT max(j.created_at) FROM jobs j WHERE j.tenant_id = t.id) AS last_run"
            " FROM tenants t WHERE NOT t.is_internal AND t.is_active ORDER BY lower(t.name)"
        )
    )
    return [
        ClientRow(
            client_id=str(r.id),
            client_name=r.name,
            runs=r.runs,
            success_rate=None if r.done == 0 else r.ok / r.done,
            machines_total=r.m_total,
            machines_online=r.m_online,
            machines_no_signal=r.m_silent,
            last_run_at=r.last_run,
        )
        for r in rows
    ]


async def _client_machines(db: Any) -> tuple[list[MachineRow], list[Silent]]:
    # Machines that need a look come first (no signal, then busy), so the cut never hides them.
    rows = (
        await db.execute(
            text(
                "SELECT m.id, m.name, m.status, m.last_seen_at, m.pool_id, cur.id AS cur_id,"
                " cur.short_code AS cur_code, cur.status AS cur_status"
                " FROM machines m JOIN tenants t ON t.id = m.tenant_id"
                " LEFT JOIN LATERAL (SELECT j.id, j.short_code, j.status FROM jobs j"
                "   WHERE j.tenant_id = m.tenant_id AND j.machine_id = m.id"
                "   AND j.status IN ('assigned', 'running') LIMIT 1) cur ON true"
                " WHERE NOT t.is_internal AND m.status <> 'revoked'"
                " ORDER BY (m.status = 'offline') DESC, (cur.id IS NOT NULL) DESC,"
                " lower(m.name), m.id LIMIT 12"
            )
        )
    ).all()
    machines = [
        MachineRow(
            id=str(r.id),
            name=r.name,
            status=r.status,
            last_seen_at=r.last_seen_at,
            current_job=None
            if r.cur_id is None
            else CurrentJob(id=r.cur_id, short_code=r.cur_code, status=r.cur_status),
        )
        for r in rows
    ]
    # The banner does not depend on the cut above: the oldest silences, and who takes over.
    silent_rows = (
        await db.execute(
            text(
                "SELECT m.id, m.name, m.mode, m.last_seen_at,"
                " (SELECT o.name FROM machines o WHERE o.tenant_id = m.tenant_id"
                "   AND o.pool_id = m.pool_id AND o.status = 'online'"
                "   ORDER BY lower(o.name) LIMIT 1) AS other_online"
                " FROM machines m JOIN tenants t ON t.id = m.tenant_id"
                " WHERE NOT t.is_internal AND m.status = 'offline'"
                " ORDER BY m.last_seen_at NULLS LAST, m.id LIMIT 5"
            )
        )
    ).all()
    silent = [
        Silent(
            id=str(r.id),
            name=r.name,
            mode=r.mode,
            last_seen_at=r.last_seen_at,
            other_online=r.other_online,
        )
        for r in silent_rows
    ]
    return machines, silent
