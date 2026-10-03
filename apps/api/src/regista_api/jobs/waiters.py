"""Long-polling without one database connection per waiting agent (docs/adr/0020).

Agents that find no run wait here, in memory, as a handful of bytes each. One connection per API
process, made with asyncpg and kept out of the SQLAlchemy pool, `LISTEN`s on `regista_jobs`; each
notification (`<tenant_id>:<pool_id>`, sent by a trigger when a run is created) wakes only the
agents of that client and pool. A woken agent asks the database again, so a notification is only
ever a hint: a missed one delays a run until the next safety tick, it never loses it.

If the listening connection drops, the listener reconnects with a growing pause and wakes
everybody, because notifications may have been missed in between.
"""

import asyncio
import contextlib
import re
import uuid
from collections.abc import Iterator

import asyncpg
import structlog

log = structlog.get_logger()

CHANNEL = "regista_jobs"
Key = tuple[uuid.UUID, uuid.UUID]  # (tenant_id, pool_id)

_PING_SECONDS = 30
_PING_TIMEOUT_SECONDS = 10
_BACKOFF_START = 0.5
_BACKOFF_MAX = 15.0


class TooManyWaiters(Exception):
    """This process already holds as many waiting agents as it allows."""


class JobWaiters:
    def __init__(self, max_waiters: int) -> None:
        self._max = max_waiters
        self._events: dict[Key, set[asyncio.Event]] = {}
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @contextlib.contextmanager
    def register(self, tenant_id: uuid.UUID, pool_id: uuid.UUID) -> Iterator[asyncio.Event]:
        if self._count >= self._max:
            raise TooManyWaiters
        key = (tenant_id, pool_id)
        event = asyncio.Event()
        self._events.setdefault(key, set()).add(event)
        self._count += 1
        try:
            yield event
        finally:
            self._count -= 1
            bucket = self._events.get(key)
            if bucket is not None:
                bucket.discard(event)
                if not bucket:
                    del self._events[key]

    def wake(self, tenant_id: uuid.UUID, pool_id: uuid.UUID) -> None:
        for event in self._events.get((tenant_id, pool_id), ()):
            event.set()

    def wake_all(self) -> None:
        for bucket in self._events.values():
            for event in bucket:
                event.set()


def asyncpg_dsn(database_url: str) -> str:
    """The SQLAlchemy URL of the API (`postgresql+asyncpg://...`) as a plain asyncpg DSN."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)


class JobListener:
    def __init__(self, dsn: str, waiters: JobWaiters) -> None:
        self._dsn = dsn
        self._waiters = waiters
        self._task: asyncio.Task[None] | None = None
        self._connection: asyncpg.Connection | None = None
        self.connected = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="job-listener")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            tenant, pool = payload.split(":")
            self._waiters.wake(uuid.UUID(tenant), uuid.UUID(pool))
        except ValueError:
            # Not ours or malformed: everybody checks the database, which is always correct.
            self._waiters.wake_all()

    async def _run(self) -> None:
        backoff = _BACKOFF_START
        while True:
            try:
                await self._listen_until_it_drops()
                backoff = _BACKOFF_START
            except asyncio.CancelledError:
                raise
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                log.warning("job_listener_down", error=type(exc).__name__, retry_in=backoff)
            finally:
                self.connected.clear()
                self._waiters.wake_all()
            await asyncio.sleep(backoff)
            backoff = min(_BACKOFF_MAX, backoff * 2)

    async def _listen_until_it_drops(self) -> None:
        conn = await asyncpg.connect(self._dsn, timeout=10)
        self._connection = conn
        closed = asyncio.Event()
        conn.add_termination_listener(lambda _c: closed.set())
        try:
            await conn.add_listener(CHANNEL, self._on_notify)
            self.connected.set()
            log.info("job_listener_up")
            # Notifications sent while the connection was down are gone: ask everybody to look.
            self._waiters.wake_all()
            while not closed.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(closed.wait(), timeout=_PING_SECONDS)
                if not closed.is_set():
                    # A half-open socket never says it is dead; a failed ping does.
                    await asyncio.wait_for(conn.execute("SELECT 1"), timeout=_PING_TIMEOUT_SECONDS)
        finally:
            self._connection = None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.close(), timeout=5)
