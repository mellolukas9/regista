"""The whole story with real processes: API, worker and agent, a real Postgres and a real S3.

A person clicks "Executar agora"; the agent takes the run, executes a robot as a child process,
ships logs and a screenshot, and the panel shows the result. Then the hard parts: cancelling in
the middle, the machine going away in the middle, revoking it in the middle. Nothing the machine
proved itself with, and no secret a robot prints, may end up in any log.
"""

import asyncio
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psutil
import pytest
import pytest_asyncio
from sqlalchemy import text

from regista_api.core.db import tenant_session

from .conftest import REPO_ROOT, DbUrls, S3Env, Seed
from .test_agent_e2e import (
    Factory,
    Proc,
    Stack,
    _csrf,
    _eventually,
    go_silent,
    running_stack,
)

pytestmark = [pytest.mark.e2e, pytest.mark.s3]

FIXTURE_BOTS = REPO_ROOT / "agent" / "tests" / "fixtures" / "bots"
PNG_MAGIC = b"\x89PNG"


@pytest_asyncio.fixture
async def job_stack(
    db_urls: DbUrls, seed: Seed, app_factory: Factory, tmp_path: Path, s3_env: S3Env
) -> AsyncIterator[tuple[Stack, Factory]]:
    api_env = {
        "REGISTA_S3_ENDPOINT_URL": s3_env.endpoint_url,
        "REGISTA_S3_ACCESS_KEY_ID": s3_env.access_key_id,
        "REGISTA_S3_SECRET_ACCESS_KEY": s3_env.secret_access_key,
        "REGISTA_S3_REGION": s3_env.region,
        "REGISTA_S3_BUCKET": f"e2e-{uuid.uuid4().hex[:10]}",
        "REGISTA_AGENT_POLL_WAIT_SECONDS": "5",
    }
    # A copy of the test robot under a name of its own: package names are unique per client and
    # the tests share one.
    bots_dir = tmp_path / "bots"
    shutil.copytree(FIXTURE_BOTS / "fake_bot", bots_dir / f"fake_{uuid.uuid4().hex[:8]}")
    agent_extra = {
        "REGISTA_ENVIRONMENT": "dev",
        "REGISTA_DEV_UNSIGNED": "1",
        "REGISTA_DEV_BOTS_DIR": str(bots_dir),
        "REGISTA_DEV_DIRECT_ROBOT": "1",  # no robot host in these runs (ADR 0022)
        "REGISTA_CANCEL_GRACE_SECONDS": "2",
        "REGISTA_LOG_FLUSH_SECONDS": "0.3",
        "REGISTA_POLL_WAIT_SECONDS": "5",
        "REGISTA_JOB_PRIORITY": "normal",
    }
    async with running_stack(
        db_urls, seed, app_factory, tmp_path, api_env=api_env, agent_extra=agent_extra
    ) as stack:
        yield stack, app_factory


class Rigged:
    """A registered machine with a running agent, and a bot in its pool."""

    def __init__(self, stack: Stack, factory: Factory) -> None:
        self.stack = stack
        self.factory = factory
        self.admin = stack.admin
        self.agent: Proc | None = None
        self.machine_id = ""
        self.machine_name = ""
        self.bot_id = ""
        self.pids: set[int] = set()

    async def setup(self) -> None:
        stack, suffix = self.stack, uuid.uuid4().hex[:8]
        pool = await self.admin.post(
            "/pools", json={"name": f"pool-{suffix}"}, headers=_csrf(self.admin)
        )
        assert pool.status_code == 201, pool.text
        pool_id = pool.json()["id"]
        package = os.listdir(stack.agent_extra["REGISTA_DEV_BOTS_DIR"])[0]
        async with tenant_session(self.factory, tenant_id=stack.tenant_id) as db:
            self.bot_id = str(
                (
                    await db.execute(
                        text(
                            "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
                            " VALUES (:t, :p, :n, :k) RETURNING id"
                        ),
                        {"t": stack.tenant_id, "p": pool_id, "n": f"Bot {suffix}", "k": package},
                    )
                ).scalar_one()
            )
        self.machine_name = f"estacao-{suffix}"
        created = await self.admin.post(
            "/machines",
            json={"name": self.machine_name, "pool_id": pool_id, "mode": "service"},
            headers=_csrf(self.admin),
        )
        assert created.status_code == 201, created.text
        self.machine_id = created.json()["machine_id"]
        enrolled = stack.agent_blocking(
            "enroll", "--url", stack.url, "--key", created.json()["enrollment_key"]
        )
        assert enrolled.returncode == 0, enrolled.stdout + enrolled.stderr
        self.start_agent("agent-1.log")

        async def online() -> bool:
            self.check_agent()
            return (await self.machine())["status"] == "online"

        await _eventually(online, seconds=30, what="the machine to be online")

    def start_agent(self, log: str) -> Proc:
        self.agent = self.stack.agent("run", log=log)
        self.stack.processes.append(self.agent)
        return self.agent

    def check_agent(self) -> None:
        assert self.agent is not None
        if self.agent.popen.poll() is not None:
            raise AssertionError(f"the agent exited early:\n{self.agent.output()}")

    async def machine(self) -> dict[str, object]:
        r = await self.admin.get(f"/machines/{self.machine_id}")
        assert r.status_code == 200, r.text
        body: dict[str, object] = r.json()
        return body

    async def run(self, mode: str, **params: object) -> str:
        r = await self.admin.post(
            "/jobs",
            json={"bot_id": self.bot_id, "params": {"mode": mode, **params}},
            headers=_csrf(self.admin),
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    async def job(self, job_id: str) -> dict[str, object]:
        r = await self.admin.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body: dict[str, object] = r.json()
        return body

    async def logs(self, job_id: str) -> list[dict[str, str]]:
        r = await self.admin.get(f"/jobs/{job_id}/logs", params={"limit": 1000})
        assert r.status_code == 200, r.text
        items: list[dict[str, str]] = r.json()["items"]
        return items

    async def wait_status(self, job_id: str, *statuses: str, seconds: float = 40) -> None:
        async def reached() -> bool:
            return (await self.job(job_id))["status"] in statuses

        await _eventually(reached, seconds=seconds, what=f"the run to be {statuses}")

    async def wait_log(self, job_id: str, prefix: str, seconds: float = 30) -> str:
        found: list[str] = []

        async def seen() -> bool:
            for line in await self.logs(job_id):
                if line["message"].startswith(prefix):
                    found.append(line["message"])
                    return True
            return False

        await _eventually(seen, seconds=seconds, what=f"a log line starting with {prefix!r}")
        return found[0]

    async def robot_pid(self, job_id: str) -> int:
        pid = int((await self.wait_log(job_id, "pid ")).split()[1])
        self.pids.add(pid)
        return pid

    def kill_leftovers(self) -> None:
        for pid in self.pids:
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    child.kill()
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def _gone(pid: int) -> bool:
    if not psutil.pid_exists(pid):
        return True
    try:
        return bool(psutil.Process(pid).status() == psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return True


async def _rigged(job_stack: tuple[Stack, Factory]) -> Rigged:
    rig = Rigged(*job_stack)
    await rig.setup()
    return rig


def _no_secrets(stack: Stack, extra: list[str]) -> None:
    for proc in stack.processes:
        proc.stop()
    contents = {
        "api": stack.api.output(),
        "worker": stack.worker.output(),
        "agent file": (stack.home / "logs" / "agent.log").read_text("utf-8", errors="replace"),
        **{f"extra {i}": c for i, c in enumerate(extra)},
    }
    for where, content in contents.items():
        assert not re.search(r"rga1\.[A-Za-z0-9_\-]{10,}", content), f"{where}: an access token"
        assert not re.search(r"rgk_[A-Za-z0-9_\-]{20,}", content), f"{where}: an enrollment key"
        assert "hunter2" not in content, f"{where}: a secret a robot printed"


# --- the happy path ---------------------------------------------------------------------------


async def test_executar_agora_runs_the_robot_and_the_panel_shows_everything(
    job_stack: tuple[Stack, Factory],
) -> None:
    rig = await _rigged(job_stack)
    try:
        job_id = await rig.run("ok")
        await rig.wait_status(job_id, "completed", seconds=45)

        job = await rig.job(job_id)
        assert job["machine_name"] == rig.machine_name and job["error_code"] is None
        assert job["assigned_at"] and job["started_at"] and job["finished_at"]
        assert str(job["triggered_by"]).endswith("@example.com")

        logs = await rig.logs(job_id)
        by_message = {line["message"]: line["level"] for line in logs}
        assert by_message["robo iniciado"] == "INFO"
        assert by_message["passo dois"] == "WARN"
        assert by_message["fim"] == "INFO"

        artifacts = (await rig.admin.get(f"/jobs/{job_id}/artifacts")).json()["items"]
        assert len(artifacts) == 1 and artifacts[0]["content_type"] == "image/png"
        view = await rig.admin.get(f"/artifacts/{artifacts[0]['id']}/content")
        assert view.status_code == 302
        async with httpx.AsyncClient() as plain:
            image = await plain.get(view.headers["location"])
        assert image.status_code == 200 and image.content.startswith(PNG_MAGIC)

        # The machine is free again, and the history of the run is in the list.
        assert (await rig.machine())["current_job"] is None
        listed = (await rig.admin.get("/jobs")).json()["items"]
        assert job_id in {i["id"] for i in listed}
        _no_secrets(rig.stack, [])
    finally:
        rig.kill_leftovers()


async def test_what_a_robot_prints_never_keeps_a_secret(job_stack: tuple[Stack, Factory]) -> None:
    rig = await _rigged(job_stack)
    try:
        job_id = await rig.run("secrets")
        await rig.wait_status(job_id, "completed", seconds=45)
        text_of_logs = "\n".join(line["message"] for line in await rig.logs(job_id))
        for leaked in ("hunter2", "rga1.AAAA", "abc123", "rgk_zzzz", "\x1b"):
            assert leaked not in text_of_logs
        assert "vermelho" in text_of_logs
        _no_secrets(rig.stack, [])
    finally:
        rig.kill_leftovers()


# --- cancelling in the middle -----------------------------------------------------------------


async def test_cancelling_in_the_middle_stops_the_robot_and_everything_it_started(
    job_stack: tuple[Stack, Factory],
) -> None:
    rig = await _rigged(job_stack)
    try:
        job_id = await rig.run("stubborn")
        await rig.wait_status(job_id, "running")
        robot_pid = await rig.robot_pid(job_id)
        child_pid = int((await rig.wait_log(job_id, "child ")).split()[1])
        rig.pids.add(child_pid)
        assert not _gone(robot_pid) and not _gone(child_pid)

        cancel = await rig.admin.post(f"/jobs/{job_id}/cancel", headers=_csrf(rig.admin))
        assert cancel.status_code == 200 and cancel.json()["cancel_requested_at"]
        await rig.wait_status(job_id, "cancelled", seconds=30)

        async def stopped() -> bool:
            return _gone(robot_pid) and _gone(child_pid)

        await _eventually(stopped, seconds=15, what="the robot and its child to be gone")
        assert (await rig.job(job_id))["error_code"] is None
        assert (await rig.machine())["current_job"] is None

        # The machine is free: the next run goes through.
        again = await rig.run("ok")
        await rig.wait_status(again, "completed", seconds=45)
        _no_secrets(rig.stack, [])
    finally:
        rig.kill_leftovers()


# --- the machine going away in the middle -----------------------------------------------------


async def test_a_machine_that_goes_away_in_the_middle_makes_the_run_machine_lost(
    job_stack: tuple[Stack, Factory],
) -> None:
    rig = await _rigged(job_stack)
    try:
        job_id = await rig.run("wait")
        await rig.wait_status(job_id, "running")
        await rig.robot_pid(job_id)

        assert rig.agent is not None
        rig.agent.popen.kill()  # no goodbye: a power cut, not a clean stop
        await asyncio.to_thread(rig.agent.popen.wait, 15)

        await go_silent(rig.stack, rig.machine_id)
        await rig.wait_status(job_id, "failed", seconds=40)
        job = await rig.job(job_id)
        assert job["error_code"] == "machine_lost" and job["finished_at"]
        assert (await rig.machine())["status"] == "offline"
        assert any(line["message"] == "robo iniciado" for line in await rig.logs(job_id))

        # Back again: the machine is online, the run stays failed, and the next one runs.
        rig.kill_leftovers()
        rig.start_agent("agent-2.log")

        async def back() -> bool:
            rig.check_agent()
            return (await rig.machine())["status"] == "online"

        await _eventually(back, seconds=30, what="the machine to come back")
        assert (await rig.job(job_id))["status"] == "failed"
        again = await rig.run("ok")
        await rig.wait_status(again, "completed", seconds=45)
    finally:
        rig.kill_leftovers()


# --- revoking in the middle -------------------------------------------------------------------


async def test_revoking_the_machine_in_the_middle_cancels_the_run_and_stops_the_agent(
    job_stack: tuple[Stack, Factory],
) -> None:
    rig = await _rigged(job_stack)
    try:
        job_id = await rig.run("stubborn")
        await rig.wait_status(job_id, "running")
        robot_pid = await rig.robot_pid(job_id)

        revoked = await rig.admin.post(
            f"/machines/{rig.machine_id}/revoke",
            json={"confirm_name": rig.machine_name},
            headers=_csrf(rig.admin),
        )
        assert revoked.status_code == 200, revoked.text

        job = await rig.job(job_id)
        assert job["status"] == "cancelled" and job["error_code"] == "machine_revoked"

        assert rig.agent is not None
        try:
            code = await asyncio.to_thread(rig.agent.popen.wait, 30)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the revoked agent kept running:\n{rig.agent.output()}")
        assert code == 3, rig.agent.output()

        async def robot_gone() -> bool:
            return _gone(robot_pid)

        await _eventually(robot_gone, seconds=15, what="the robot to be stopped with the agent")
        _no_secrets(rig.stack, [])
    finally:
        rig.kill_leftovers()
