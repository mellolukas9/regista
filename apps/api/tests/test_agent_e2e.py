"""A real agent against a real API and a real worker, as three separate processes.

Everything the unit tests fake is real here: HTTP over a socket, the process that marks machines
as "Sem sinal", and the agent's own command line with its key file. The story is the one an
operator lives through: register a machine in the panel, enroll the agent, see it online, stop it
and see "Sem sinal", start it again, revoke it in the panel and watch the running agent stop.
At the end, nothing the machine proved itself with may be in any of the three logs.
"""

import asyncio
import base64
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_agent.keystore import KeyStore
from regista_api.auth import mfa
from regista_api.core.db import tenant_session
from regista_api.core.keys import LocalKeyProvider
from regista_api.core.security import hash_password

from .conftest import DbUrls, Seed

pytestmark = pytest.mark.e2e

Factory = async_sessionmaker[AsyncSession]

HEARTBEAT_SECONDS = 1
# Far longer than any run: a live agent never turns "Sem sinal" because the test machine was busy.
# Tests that need a silent machine move that machine's clock instead (`go_silent`), so nothing
# depends on how long a real silence takes.
OFFLINE_AFTER_SECONDS = 3600
PASSWORD = "uma-senha-bem-longa-e-unica-1"
REVOKED_TEXT = "Esta máquina foi revogada no Regista"
REJECTED_TEXT = "não aceita mais a identidade desta máquina"

# On Windows the key folder is locked down with an ACL that only an administrator can write in,
# and these runs are not elevated everywhere. The ACL has tests of its own (agent/tests); here the
# agent's command line runs with that one step stood in for.
_WINDOWS_AGENT = (
    "from regista_agent import _windows;"
    "_windows.resolve_sid = lambda account: 'S-1-5-21-1-2-3-1000';"
    "_windows.restrict_directory = lambda directory, sid: None;"
    "from regista_agent.cli import main; main()"
)
AGENT_COMMAND = (
    [sys.executable, "-c", _WINDOWS_AGENT]
    if sys.platform == "win32"
    else [sys.executable, "-m", "regista_agent"]
)


@dataclass
class Proc:
    popen: subprocess.Popen[bytes]
    log: Path
    handle: IO[bytes]

    def stop(self) -> None:
        if self.popen.poll() is None:
            self.popen.terminate()
            try:
                self.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait(timeout=10)
        self.handle.close()

    def output(self) -> str:
        return self.log.read_text("utf-8", errors="replace")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _clean_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("REGISTA_")}
    for proxy in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        env.pop(proxy, None)
    env["PYTHONUNBUFFERED"] = "1"
    # What the processes print is read back as UTF-8, whatever the machine's own code page is.
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _spawn(argv: list[str], env: dict[str, str], log: Path, cwd: Path) -> Proc:
    handle = log.open("wb")
    popen = subprocess.Popen(  # noqa: S603  (our own interpreter and modules)
        argv, env=env, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT
    )
    return Proc(popen, log, handle)


async def _eventually(
    check: Callable[[], Awaitable[bool]], *, seconds: float, what: str, interval: float = 0.4
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out after {seconds}s waiting for: {what}")


@dataclass
class Stack:
    url: str
    home: Path
    tmp: Path
    env: dict[str, str]  # for processes that talk to the API (the agent adds its own)
    api: Proc
    worker: Proc
    admin: httpx.AsyncClient
    tenant_id: uuid.UUID
    master_key: str
    processes: list[Proc]
    factory: Factory
    # Extra settings of the agent (the job tests turn on the development mode through these).
    agent_extra: dict[str, str] = field(default_factory=dict)

    def agent_env(self) -> dict[str, str]:
        return _clean_env(
            {"REGISTA_HOME": str(self.home), "REGISTA_LOG_LEVEL": "DEBUG", **self.agent_extra}
        )

    def agent(self, *args: str, log: str) -> Proc:
        return _spawn(AGENT_COMMAND + list(args), self.agent_env(), self.tmp / log, self.tmp)

    def agent_blocking(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            AGENT_COMMAND + list(args),
            env=self.agent_env(),
            cwd=self.tmp,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )


async def _admin_login(
    url: str, factory: Factory, tenant_id: uuid.UUID, master_key: str
) -> httpx.AsyncClient:
    """An administrator of the client with MFA already set up, created straight in the database
    (the sign-up flow has its own tests), then signed in over the real API with a real code."""
    keys = LocalKeyProvider(master_key)
    secret = mfa.new_secret()
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        user_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO users (tenant_id, email, role, status, password_hash)"
                    " VALUES (:t, :e, 'tenant_admin', 'active', :p) RETURNING id"
                ),
                {"t": tenant_id, "e": email, "p": hash_password(PASSWORD)},
            )
        ).scalar_one()
        sealed, key_id = mfa.encrypt_secret(keys, tenant_id, user_id, secret)
        await db.execute(
            text(
                "UPDATE users SET mfa_secret_enc = :s, mfa_key_id = :k, mfa_enabled = true,"
                " mfa_enabled_at = now() WHERE id = :u"
            ),
            {"s": sealed, "k": key_id, "u": user_id},
        )

    client = httpx.AsyncClient(base_url=url, timeout=20)
    first = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert first.status_code == 200, first.text
    verified = await client.post(
        "/auth/mfa/verify",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"X-CSRF-Token": client.cookies.get("regista_csrf") or ""},
    )
    assert verified.status_code == 200, verified.text
    return client


@pytest_asyncio.fixture
async def stack(
    db_urls: DbUrls, seed: Seed, app_factory: Factory, tmp_path: Path
) -> AsyncIterator[Stack]:
    async with running_stack(db_urls, seed, app_factory, tmp_path) as running:
        yield running


@asynccontextmanager
async def running_stack(
    db_urls: DbUrls,
    seed: Seed,
    app_factory: Factory,
    tmp_path: Path,
    *,
    api_env: dict[str, str] | None = None,
    agent_extra: dict[str, str] | None = None,
) -> AsyncIterator[Stack]:
    """The real API and worker as processes, and a client already logged in. `api_env` adds
    settings of the API and the worker; `agent_extra` settings of the agents started from it."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    master_key = LocalKeyProvider.generate_key()
    env = _clean_env(
        {
            "REGISTA_ENVIRONMENT": "test",
            "REGISTA_DATABASE_URL": db_urls.app,
            "REGISTA_DATABASE_OWNER_URL": db_urls.owner,
            "REGISTA_MASTER_KEY": master_key,
            "REGISTA_EMAIL_BACKEND": "memory",
            "REGISTA_LOG_LEVEL": "INFO",
            "REGISTA_API_PUBLIC_URL": url,
            "REGISTA_HEARTBEAT_SECONDS": str(HEARTBEAT_SECONDS),
            "REGISTA_MACHINE_OFFLINE_AFTER_SECONDS": str(OFFLINE_AFTER_SECONDS),
            "REGISTA_MACHINE_SWEEP_CRON": "* * * * * *",
            **(api_env or {}),
        }
    )
    api = _spawn(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "regista_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env,
        tmp_path / "api.log",
        tmp_path,
    )
    worker = _spawn(
        [sys.executable, "-m", "regista_api.tasks.worker"], env, tmp_path / "worker.log", tmp_path
    )
    processes = [api, worker]
    admin: httpx.AsyncClient | None = None
    try:
        async with httpx.AsyncClient(base_url=url, timeout=5) as probe:

            async def api_is_up() -> bool:
                if api.popen.poll() is not None:
                    raise AssertionError(f"the API exited early:\n{api.output()}")
                try:
                    return (await probe.get("/health")).status_code == 200
                except httpx.TransportError:
                    return False

            await _eventually(api_is_up, seconds=40, what="the API to answer /health")
        admin = await _admin_login(url, app_factory, seed.tenant_a, master_key)
        yield Stack(
            url=url,
            home=tmp_path / "agent-home",
            tmp=tmp_path,
            env=env,
            api=api,
            worker=worker,
            admin=admin,
            tenant_id=seed.tenant_a,
            master_key=master_key,
            processes=processes,
            factory=app_factory,
            agent_extra=agent_extra or {},
        )
    finally:
        if admin is not None:
            await admin.aclose()
        for proc in processes:
            proc.stop()


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("regista_csrf") or ""}


async def _machine(stack: Stack, machine_id: str) -> dict[str, object]:
    response = await stack.admin.get(f"/machines/{machine_id}")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def go_silent(stack: Stack, machine_id: str, *, seconds: float = 30) -> None:
    """Make the machine silent for longer than the "Sem sinal" limit by moving its last signal
    back in time, then wait for the real worker to notice. Call it after the agent is gone: the
    wait repeats the move, so a heartbeat that was still in flight cannot undo it."""

    async def silent() -> bool:
        async with tenant_session(stack.factory, tenant_id=stack.tenant_id) as db:
            await db.execute(
                text(
                    "UPDATE machines SET last_seen_at = now() - interval '2 hours'"
                    " WHERE id = :m AND status = 'online'"
                ),
                {"m": uuid.UUID(machine_id)},
            )
        return (await _machine(stack, machine_id))["status"] == "offline"

    await _eventually(silent, seconds=seconds, what="the worker to mark the machine as silent")


async def _events(stack: Stack, machine_id: str) -> list[str]:
    response = await stack.admin.get(f"/machines/{machine_id}/events", params={"per_page": 50})
    assert response.status_code == 200, response.text
    return [e["kind"] for e in reversed(response.json()["items"])]


async def test_a_real_agent_enrolls_goes_silent_comes_back_and_is_refused_after_revocation(
    stack: Stack,
) -> None:
    admin = stack.admin
    suffix = uuid.uuid4().hex[:8]

    # 1. The administrator registers the machine in the panel and gets the one-time key.
    pool = await admin.post("/pools", json={"name": f"pool-{suffix}"}, headers=_csrf(admin))
    assert pool.status_code == 201, pool.text
    name = f"estacao-{suffix}"
    created = await admin.post(
        "/machines",
        json={"name": name, "pool_id": pool.json()["id"], "mode": "service"},
        headers=_csrf(admin),
    )
    assert created.status_code == 201, created.text
    machine_id, enrollment_key = created.json()["machine_id"], created.json()["enrollment_key"]

    # 2. On the machine: enroll.
    enrolled = stack.agent_blocking("enroll", "--url", stack.url, "--key", enrollment_key)
    assert enrolled.returncode == 0, enrolled.stdout + enrolled.stderr
    assert machine_id in enrolled.stdout
    assert (await _machine(stack, machine_id))["status"] == "online"

    # 3. Run: online, with the first signal in its history.
    agent = stack.agent("run", log="agent-1.log")
    stack.processes.append(agent)

    async def has_first_signal() -> bool:
        if agent.popen.poll() is not None:
            raise AssertionError(f"the agent exited early:\n{agent.output()}")
        return "first_signal" in await _events(stack, machine_id)

    await _eventually(has_first_signal, seconds=30, what="the first signal")
    detail = await _machine(stack, machine_id)
    assert detail["status"] == "online" and detail["agent_version"]
    assert detail["os_info"]

    # 4. Kill the agent: after the silence the worker marks the machine "Sem sinal".
    agent.stop()

    await go_silent(stack, machine_id)
    assert "went_offline" in await _events(stack, machine_id)

    summary = (await admin.get("/machines/summary")).json()
    assert summary["no_signal"] >= 1

    # 5. Start it again: it comes back.
    agent = stack.agent("run", log="agent-2.log")
    stack.processes.append(agent)

    async def came_back() -> bool:
        if agent.popen.poll() is not None:
            raise AssertionError(f"the agent exited early:\n{agent.output()}")
        return (await _machine(stack, machine_id))["status"] == "online"

    await _eventually(came_back, seconds=30, what="the machine to come back online")
    assert "came_back" in await _events(stack, machine_id)

    # 6. Revoke it in the panel: the running agent stops by itself, with the revoked exit code.
    revoked = await admin.post(
        f"/machines/{machine_id}/revoke", json={"confirm_name": name}, headers=_csrf(admin)
    )
    assert revoked.status_code == 200, revoked.text
    try:
        code = await asyncio.to_thread(agent.popen.wait, 30)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the revoked agent kept running:\n{agent.output()}")
    assert code == 3, agent.output()
    assert REVOKED_TEXT in agent.output()

    # 7. Starting again is refused the same way, and the used key is worth nothing.
    again = stack.agent_blocking("run")
    assert again.returncode == 3, again.stdout + again.stderr
    assert REJECTED_TEXT in again.stderr or REVOKED_TEXT in again.stderr
    reused = stack.agent_blocking("enroll", "--url", stack.url, "--key", enrollment_key, "--force")
    assert reused.returncode == 1, reused.stdout + reused.stderr
    assert "recusou a chave de registro" in reused.stderr
    assert (await _machine(stack, machine_id))["status"] == "revoked"

    # 8. The history tells the whole story.
    kinds = await _events(stack, machine_id)
    assert kinds[0] == "enrolled" and kinds[-1] == "revoked"
    assert {"first_signal", "went_offline", "came_back"} <= set(kinds)

    # 9. Nothing the machine proved itself with is in any log.
    for proc in stack.processes:
        proc.stop()
    store = KeyStore(stack.home / "keys", agent_account=None)
    private = store.load().private_bytes_raw()
    secrets_to_find = {
        enrollment_key,
        base64.b64encode(private).decode(),
        base64.urlsafe_b64encode(private).decode(),
        private.hex(),
    }
    logs = {
        "api": stack.api.output(),
        "worker": stack.worker.output(),
        "agent run 1": (stack.tmp / "agent-1.log").read_text("utf-8", errors="replace"),
        "agent run 2": (stack.tmp / "agent-2.log").read_text("utf-8", errors="replace"),
        "agent file": (stack.home / "logs" / "agent.log").read_text("utf-8", errors="replace"),
        "agent enroll": enrolled.stdout + enrolled.stderr,
    }
    assert logs["api"].count("http_request") >= 10, "the API log is empty: the check would be too"
    for where, content in logs.items():
        for secret in secrets_to_find:
            assert secret not in content, f"{where}: a secret is in the log: {secret[:8]}..."
        # No enrollment key or access token of any value, only the redacted forms.
        assert not re.search(r"rgk_[A-Za-z0-9_\-]{20,}", content), f"{where}: an enrollment key"
        assert not re.search(r"rga1\.[A-Za-z0-9_\-]{10,}", content), f"{where}: an access token"
