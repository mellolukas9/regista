"""Taking runs from the server and running them (docs/specs/agent.md).

One run at a time, which is also what the server enforces. A heartbeat thread keeps telling the
server the machine is alive and learns of cancellations; the main thread polls for work, runs the
robot, ships logs and screenshots, and reports the result.

Every outcome has a code the server understands. The robot's own words only travel as a short,
scrubbed message. A revoked machine kills the robot at once and stops.
"""

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from regista_agent import robot
from regista_agent.config import AgentSettings
from regista_agent.errors import MachineRevoked, ServerUnavailable
from regista_agent.jobapi import Assignment, JobApi, JobGone
from regista_agent.shipper import MAX_LINE_BYTES, LogShipper, clean

log = logging.getLogger("regista_agent")

_POLL_PAUSE_SECONDS = 5.0
_SCREENSHOT_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_SCREENSHOTS = 20
MAX_SCREENSHOT_BYTES = 5_000_000


class JobState:
    """What the heartbeat thread and the run share: which run is on, and whether to stop it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job_id: str | None = None
        self.cancel = threading.Event()

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._job_id

    def begin(self, job_id: str) -> None:
        with self._lock:
            self._job_id = job_id
            self.cancel.clear()

    def end(self) -> None:
        with self._lock:
            self._job_id = None
            self.cancel.clear()

    def cancellations(self, ids: list[str]) -> None:
        """Called by the heartbeat with what the server says must stop."""
        with self._lock:
            if self._job_id is not None and self._job_id in ids:
                self.cancel.set()


@dataclass(frozen=True)
class Outcome:
    kind: str  # "completed" | "failed" | "cancelled"
    code: str = ""
    message: str = ""


class JobExecutor:
    def __init__(
        self,
        settings: AgentSettings,
        api: JobApi,
        state: JobState,
        stop: threading.Event,
        *,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._api = api
        self._state = state
        self._stop = stop
        self._base_env = dict(os.environ if base_env is None else base_env)

    # --- the loop -----------------------------------------------------------------------------

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                assignment = self._api.next_job(self._settings.poll_wait_seconds)
            except ServerUnavailable as exc:
                log.warning("no work asked for: %s", exc)
                self._stop.wait(_POLL_PAUSE_SECONDS)
                continue
            if assignment is None:
                continue
            self.execute(assignment)

    # --- one run ------------------------------------------------------------------------------

    def execute(self, job: Assignment) -> None:
        self._state.begin(job.job_id)
        started = time.monotonic()
        log.info("run %s taken: robot %s", job.short_code, job.package_name)
        outcome = Outcome("failed", "internal", "Erro interno do agente.")
        try:
            outcome = self._run(job)
        except JobGone as exc:
            log.warning("run %s is no longer ours: %s", job.short_code, exc)
            return
        except MachineRevoked:
            log.error("machine revoked while running %s", job.short_code)
            raise
        except Exception:
            log.exception("run %s failed inside the agent", job.short_code)
        finally:
            self._state.end()
        self._report(job, outcome, seconds=time.monotonic() - started)

    def _report(self, job: Assignment, outcome: Outcome, *, seconds: float) -> None:
        try:
            if outcome.kind == "completed":
                self._api.complete(job.job_id)
            else:
                code = "cancelled" if outcome.kind == "cancelled" else outcome.code
                self._api.fail(job.job_id, code, outcome.message)
        except JobGone as exc:
            log.warning(
                "run %s ended but the server no longer wants the report: %s", job.short_code, exc
            )
        except ServerUnavailable as exc:
            log.error("could not report the end of run %s: %s", job.short_code, exc)
        # The local record the customer's IT can audit (robot, run, result).
        log.info(
            "run %s of robot %s ended: %s %s (%.0fs)",
            job.short_code,
            job.package_name,
            outcome.kind,
            outcome.code,
            seconds,
        )

    def _run(self, job: Assignment) -> Outcome:
        ack = self._api.start(job.job_id)
        if ack.cancel_requested or self._state.cancel.is_set():
            return Outcome("cancelled")
        entry = robot.resolve_dev_robot(self._settings.dev_bots_dir, job.package_name)
        if entry is None:
            return Outcome("failed", "robot_not_found", "O robô não foi encontrado nesta máquina.")

        with tempfile.TemporaryDirectory(prefix="regista-job-") as folder:
            work = Path(folder)
            artifacts = work / "artifacts"
            temp = work / "tmp"
            artifacts.mkdir()
            temp.mkdir()
            cancel_file = work / "cancel"
            shipper = LogShipper(
                self._api, job.job_id, flush_seconds=self._settings.log_flush_seconds
            )
            shipper.start()
            try:
                outcome = self._supervise(job, entry, shipper, artifacts, temp, cancel_file)
            finally:
                shipper.close()
            self._upload_screenshots(job, artifacts)
            return outcome

    def _supervise(
        self,
        job: Assignment,
        entry: Path,
        shipper: LogShipper,
        artifacts: Path,
        temp: Path,
        cancel_file: Path,
    ) -> Outcome:
        env = robot.build_env(
            base=self._base_env,
            job_id=job.job_id,
            params_json=json.dumps(job.params, ensure_ascii=False),
            artifacts_dir=artifacts,
            cancel_file=cancel_file,
            temp_dir=temp,
        )
        python = str(self._settings.dev_python or _current_python())
        process = robot.start(
            python=python,
            entry=entry,
            env=env,
            cancel_file=cancel_file,
            priority=self._settings.job_priority,
            on_stdout=lambda line: shipper.add("stdout", line),
            on_stderr=lambda line: shipper.add("stderr", line),
        )
        deadline = time.monotonic() + job.timeout_seconds
        grace = float(self._settings.cancel_grace_seconds)
        try:
            while True:
                code = process.poll()
                if code is not None:
                    process.join_readers()
                    process.kill_tree()  # whatever the robot left behind
                    if code == 0:
                        return Outcome("completed")
                    return Outcome("failed", "robot_failed", _failure_message(code, shipper))
                if self._stop.is_set():
                    process.stop(grace_seconds=0)
                    process.join_readers()
                    return Outcome(
                        "failed", "internal", "O agente foi encerrado durante a execução."
                    )
                if self._state.cancel.is_set():
                    shipper.note("WARN", "Cancelamento pedido. Parando o robô.")
                    process.stop(grace_seconds=grace)
                    process.join_readers()
                    return Outcome("cancelled")
                if time.monotonic() >= deadline:
                    shipper.note("ERROR", "O robô passou do tempo máximo e foi parado.")
                    process.stop(grace_seconds=grace)
                    process.join_readers()
                    return Outcome("failed", "timeout", "O robô passou do tempo máximo.")
                time.sleep(0.2)
        except BaseException:
            # A revoked machine or Ctrl+C: nothing may keep running behind our back.
            process.stop(grace_seconds=0)
            raise

    # --- screenshots --------------------------------------------------------------------------

    def _upload_screenshots(self, job: Assignment, artifacts: Path) -> None:
        files = sorted(
            p
            for p in artifacts.iterdir()
            if p.is_file() and p.suffix.lower() in _SCREENSHOT_SUFFIXES
        )
        for path in files[:MAX_SCREENSHOTS]:
            size = path.stat().st_size
            if size == 0 or size > MAX_SCREENSHOT_BYTES:
                log.warning("screenshot %s skipped (size %s)", path.name, size)
                continue
            try:
                data = path.read_bytes()
                artifact_id, url, headers = self._api.presign(
                    job.job_id, _SCREENSHOT_SUFFIXES[path.suffix.lower()], len(data)
                )
                self._api.upload(url, headers, data)
                self._api.confirm_upload(artifact_id)
            except (JobGone, ServerUnavailable, OSError) as exc:
                # A missing screenshot never fails the run.
                log.warning("screenshot %s not uploaded: %s", path.name, exc)


def _current_python() -> str:
    import sys

    return sys.executable


def _failure_message(code: int, shipper: LogShipper) -> str:
    tail = " | ".join(list(shipper.tail)[-3:])
    text = f"O robô terminou com código {code}." + (f" {tail}" if tail else "")
    return clean(text)[:MAX_LINE_BYTES]
