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
import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from regista_agent import (
    environment,
    launcher,
    layout,
    packages,
    policy,
    robot,
    rundir,
    runtime,
    safefs,
    trust,
)
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError, MachineRevoked, ServerUnavailable
from regista_agent.jobapi import Assignment, JobApi, JobGone
from regista_agent.keystore import KeyStore
from regista_agent.shipper import MAX_LINE_BYTES, LogShipper, clean
from regista_pkg import PackageError

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
    # Why a package was refused (closed list); only with the code `package_invalid`.
    reason: str | None = None


@dataclass(frozen=True)
class Prepared:
    """What `_prepare` hands to the supervisor: the file to run, with which Python, and where the
    robot finds its browsers (None: whatever the environment says, development only)."""

    entry: Path
    python: str
    browsers: Path | None
    # A robot from a development folder (no signature, no run folder for its code): always started
    # directly, never through the host, which only runs what lives under a run folder.
    unsigned: bool = False


class JobExecutor:
    def __init__(
        self,
        settings: AgentSettings,
        api: JobApi,
        state: JobState,
        stop: threading.Event,
        *,
        base_env: Mapping[str, str] | None = None,
        keys: Mapping[str, Ed25519PublicKey] | None = None,
        robot_launcher: launcher.Launcher | None = None,
    ) -> None:
        self._direct = launcher.DirectLauncher()
        if robot_launcher is None:
            if not launcher.direct_allowed(settings):
                raise AgentError(
                    "Falta o hospedeiro do robô: no Windows o robô nunca roda com a conta do "
                    "agente (ADR 0022)."
                )
            robot_launcher = self._direct
        self._launcher = robot_launcher
        self._keys = keys
        self._settings = settings
        self._api = api
        self._state = state
        self._stop = stop
        self._base_env = dict(os.environ if base_env is None else base_env)

    # --- the loop -----------------------------------------------------------------------------

    def loop(self) -> None:
        while not self._stop.is_set():
            if policy.is_paused(self._settings):
                # The local kill switch: no new run is asked for, the heartbeat goes on.
                self._stop.wait(_POLL_PAUSE_SECONDS)
                continue
            try:
                assignment = self._api.next_job(self._settings.poll_wait_seconds)
            except ServerUnavailable as exc:
                log.warning("no work asked for: %s", exc)
                self._stop.wait(_POLL_PAUSE_SECONDS)
                continue
            if assignment is None:
                continue
            if policy.is_paused(self._settings):
                # The switch went on while the request waited: hand the run back untouched.
                log.info("run %s given back: the kill switch is on", assignment.short_code)
                try:
                    self._api.release(assignment.job_id)
                except (JobGone, ServerUnavailable) as exc:
                    log.warning("run %s could not be given back: %s", assignment.short_code, exc)
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
                self._api.fail(job.job_id, code, outcome.message, outcome.reason)
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

        run = rundir.create(self._settings, *layout.identities(self._settings))
        try:
            shipper = LogShipper(
                self._api, job.job_id, flush_seconds=self._settings.log_flush_seconds
            )
            shipper.start()
            try:
                prepared = self._prepare(job, run, shipper)
                if isinstance(prepared, Outcome):
                    return prepared
                outcome = self._supervise(job, prepared, shipper, run)
            finally:
                shipper.close()
            self._upload_screenshots(job, run.artifacts)
            return outcome
        finally:
            rundir.remove(run)  # always: nothing a robot wrote is kept or reused

    # --- what to run --------------------------------------------------------------------------

    def _prepare(
        self, job: Assignment, run: rundir.RunDir, shipper: LogShipper
    ) -> Prepared | Outcome:
        """Get the robot ready, or say why it cannot run. In order: no version means a folder
        robot (development only); then the local list; then the package (hash, signature, client,
        name, version, unpacking); then the runtime; then a new environment for this run."""
        if job.bot_version_id is None:
            return self._prepare_unsigned(job)

        if not policy.is_allowed(self._settings, job.package_name):
            shipper.note(
                "ERROR",
                f"O robô {job.package_name} não está na lista de robôs permitidos desta máquina.",
            )
            return Outcome("failed", "robot_not_allowed")

        store = KeyStore(self._settings.keys_dir, agent_account=self._settings.agent_account)
        tenant_id = store.read_tenant_id()
        if tenant_id is None:
            shipper.note(
                "ERROR", "Esta máquina não conhece o próprio cliente. Cadastre-a de novo (enroll)."
            )
            return Outcome("failed", "internal", "Identidade do cliente ausente nesta máquina.")

        keys = (
            self._keys if self._keys is not None else trust.trusted_keys(self._settings.environment)
        )
        cache = packages.PackageCache(self._settings.packages_dir)
        try:
            offer = self._api.package_offer(job.bot_version_id)
            verified = cache.fetch(
                self._api,
                offer,
                packages.Expected(tenant_id, job.package_name, job.version or ""),
                keys,
            )
            packages.extract(verified, run.build)
            # The robot never sees `build`: it gets a copy of the code only, born with the
            # permissions of `package` (a move would bring the ones of `build`).
            shutil.copytree(run.build / "bot", run.package / "bot", dirs_exist_ok=True)
        except PackageError as exc:
            shipper.note("ERROR", f"Pacote recusado ({exc.reason}): {exc.detail or '-'}")
            return Outcome("failed", "package_invalid", reason=exc.reason)

        manifest = verified.signed.manifest
        try:
            base_python = runtime.require(self._settings, manifest)
        except runtime.RuntimeMissing as exc:
            shipper.note(
                "ERROR",
                f"Falta preparar esta máquina ({exc.what}). Rode regista-agent setup como "
                "administrador.",
            )
            return Outcome("failed", "runtime_missing")
        try:
            python = environment.build(
                self._settings,
                base_python=base_python,
                wheels=run.build / "wheels",
                lock=run.build / "requirements.lock",
                venv=run.venv,
            )
        except (environment.EnvironmentFailed, OSError) as exc:
            shipper.note("ERROR", f"Não foi possível montar o ambiente: {exc}")
            return Outcome("failed", "environment_failed")

        self._housekeeping(cache, job.package_name, verified.signed.sha256)
        return Prepared(
            entry=run.package / "bot" / "main.py",
            python=str(python),
            browsers=runtime.browsers_path(self._settings),
        )

    def _prepare_unsigned(self, job: Assignment) -> Prepared | Outcome:
        """No version: only a development agent, with the flag, runs a robot from a folder. Any
        other agent refuses, whatever the server says."""
        if not self._settings.dev_unsigned or self._settings.environment != "dev":
            return Outcome("failed", "robot_not_found", "O robô não foi encontrado nesta máquina.")
        entry = robot.resolve_dev_robot(self._settings.dev_bots_dir, job.package_name)
        if entry is None:
            return Outcome("failed", "robot_not_found", "O robô não foi encontrado nesta máquina.")
        return Prepared(
            entry=entry,
            python=str(self._settings.dev_python or _current_python()),
            browsers=None,
            unsigned=True,
        )

    def _housekeeping(self, cache: packages.PackageCache, package_name: str, running: str) -> None:
        """Old versions go; the one running now stays. Failing to clean never fails a run."""
        try:
            cache.prune(package_name, protect={running})
        except OSError as exc:
            log.warning("cleanup skipped: %s", exc)

    def _supervise(
        self,
        job: Assignment,
        prepared: Prepared,
        shipper: LogShipper,
        run: rundir.RunDir,
    ) -> Outcome:
        through_host = not prepared.unsigned and self._launcher is not self._direct
        env = robot.build_env(
            base=self._base_env,
            job_id=job.job_id,
            params_json=json.dumps(job.params, ensure_ascii=False),
            artifacts_dir=run.artifacts,
            cancel_file=run.cancel_file,
            temp_dir=run.tmp,
            browsers_path=prepared.browsers,
            # Through the host the robot is another identity: in `service` mode its profile is a
            # folder of the run; in `session` mode it is the dedicated user's own (the host adds
            # it), by product decision (desktop applications are configured there).
            profile_dir=run.tmp if through_host and self._settings.mode != "session" else None,
            inherit_profile=not through_host,
        )
        spec = launcher.LaunchSpec(
            run_id=run.name,
            python=prepared.python,
            entry=prepared.entry,
            env=env,
            cancel_file=run.cancel_file,
            priority=self._settings.job_priority,
        )
        try:
            process = (self._launcher if through_host else self._direct).start(
                spec,
                lambda line: shipper.add("stdout", line),
                lambda line: shipper.add("stderr", line),
            )
        except launcher.LaunchFailed as exc:
            shipper.note("ERROR", f"O robô não foi iniciado ({exc.code}): {exc.detail}")
            return Outcome("failed", exc.code)
        deadline = time.monotonic() + job.timeout_seconds
        grace = float(self._settings.cancel_grace_seconds)
        try:
            while True:
                code = process.poll()
                if code is None and process.lost:
                    shipper.note(
                        "ERROR", "O hospedeiro do robô parou de responder durante a execução."
                    )
                    process.kill_tree()
                    return Outcome("failed", launcher.HOST_UNAVAILABLE)
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
        # What the robot left in `artifacts` is not trusted: only ordinary files, never a link or a
        # junction (it could point at the agent's key), read through a handle that is checked.
        files = [
            p for p in safefs.regular_files(artifacts) if p.suffix.lower() in _SCREENSHOT_SUFFIXES
        ]
        for path in files[:MAX_SCREENSHOTS]:
            data = safefs.read_regular_file(path, MAX_SCREENSHOT_BYTES)
            if not data:
                log.warning(
                    "screenshot %s skipped (not an ordinary file, empty or too big)", path.name
                )
                continue
            try:
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
