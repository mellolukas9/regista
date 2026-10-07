"""`regista-agent run`: tell the server "I am alive" every `heartbeat_seconds`.

See docs/specs/agent.md.

One loop for every mode; what differs between modes is only what `modes.preflight` checks first.
The server decides the interval and the agent follows it. The agent also takes runs: the
heartbeat runs in its own thread and the main thread works on the runs (`jobs.py`), which only
execute signed packages. `REGISTA_DEV_UNSIGNED=1` (development only) adds robots from a folder.
"""

import logging
import threading
from typing import Protocol, cast

from regista_agent import modes, policy, sysinfo, trust
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError, MachineRevoked, NotEnrolled, ServerUnavailable
from regista_agent.jobapi import HttpJobApi, JobApi
from regista_agent.jobs import JobExecutor, JobState
from regista_agent.keystore import KeyStore
from regista_agent.transport import AgentSession, HeartbeatInfo, HttpSession, make_client

log = logging.getLogger("regista_agent")

DEFAULT_INTERVAL_SECONDS = 30


class JobsHeartbeater(Protocol):
    """The same, for the mode that also runs jobs: it says which run it is busy with."""

    def heartbeat(
        self,
        *,
        agent_version: str,
        os_info: dict[str, str],
        interactive_session: bool,
        current_job_id: str | None = None,
        paused: bool = False,
    ) -> HeartbeatInfo: ...


class Heartbeater(Protocol):
    """All the loop needs from the server connection."""

    def heartbeat(
        self, *, agent_version: str, os_info: dict[str, str], interactive_session: bool
    ) -> HeartbeatInfo: ...


def build_session(settings: AgentSettings, store: KeyStore) -> AgentSession:
    if not settings.enrolled or settings.machine_id is None or settings.server_url is None:
        raise NotEnrolled("Esta máquina ainda não foi cadastrada. Rode `regista-agent enroll`.")
    return AgentSession(
        HttpSession(make_client(settings)),
        machine_id=str(settings.machine_id),
        private_key=store.load(),
        server_url=settings.server_url,
    )


def run(
    settings: AgentSettings,
    *,
    expected_mode: str | None = None,
    stop: threading.Event | None = None,
    session: Heartbeater | None = None,
    jobs: JobApi | None = None,
) -> None:
    """Heartbeat until `stop` is set. Raises `MachineRevoked` when the server says the machine is
    gone; anything else that goes wrong with the network is logged and retried next beat."""
    stop = stop or threading.Event()
    mode = settings.mode
    if not settings.enrolled or mode is None:
        raise NotEnrolled("Esta máquina ainda não foi cadastrada. Rode `regista-agent enroll`.")
    if expected_mode is not None and expected_mode != mode:
        raise AgentError(
            f"Esta máquina está cadastrada no modo {mode}, não {expected_mode}. O modo é "
            "escolhido no cadastro da máquina, no painel."
        )
    settings.check_dev_unsigned()
    trust.trusted_keys(settings.environment)  # refuses the dev override in production
    modes.preflight(mode)

    store = KeyStore(settings.keys_dir, agent_account=settings.agent_account)
    session = session or build_session(settings, store)
    interval = DEFAULT_INTERVAL_SECONDS
    interactive = modes.is_interactive_session()
    log.info("agent started: machine=%s mode=%s", settings.machine_id, mode)

    if settings.dev_unsigned:
        log.warning(
            "DEV: robots without a version run from %s without a signature (REGISTA_DEV_UNSIGNED)",
            settings.dev_bots_dir,
        )
    # With a real connection the agent always takes runs. A bare heartbeat (no job API and a
    # session that is not the real one) is what the tests of the loop itself use.
    if jobs is None and isinstance(session, AgentSession):
        jobs = HttpJobApi(session)
    if jobs is not None:
        _run_with_jobs(settings, cast(JobsHeartbeater, session), jobs, stop, interactive)
        log.info("agent stopped")
        return

    while not stop.is_set():
        try:
            info = session.heartbeat(
                agent_version=sysinfo.agent_version(),
                os_info=sysinfo.collect(),
                interactive_session=interactive,
            )
        except ServerUnavailable as exc:
            log.warning("no signal sent: %s", exc)
        else:
            interval = max(1, info.heartbeat_seconds)
            log.debug("signal sent; next in %ss", interval)
        stop.wait(interval)
    log.info("agent stopped")


def _run_with_jobs(
    settings: AgentSettings,
    session: JobsHeartbeater,
    jobs: JobApi,
    stop: threading.Event,
    interactive: bool,
) -> None:
    state = JobState()
    fatal: list[BaseException] = []

    def beats() -> None:
        interval = DEFAULT_INTERVAL_SECONDS
        while not stop.is_set():
            try:
                info = session.heartbeat(
                    agent_version=sysinfo.agent_version(),
                    os_info=sysinfo.collect(),
                    interactive_session=interactive,
                    current_job_id=state.current,
                    paused=policy.is_paused(settings),
                )
            except ServerUnavailable as exc:
                log.warning("no signal sent: %s", exc)
            except MachineRevoked as exc:
                fatal.append(exc)
                stop.set()
                return
            else:
                interval = max(1, info.heartbeat_seconds)
                state.cancellations(info.cancellations)
            stop.wait(interval)

    thread = threading.Thread(target=beats, name="heartbeat", daemon=True)
    thread.start()
    try:
        JobExecutor(settings, jobs, state, stop).loop()
    except MachineRevoked as exc:
        fatal.append(exc)
    finally:
        stop.set()
        thread.join(timeout=10)
    if fatal:
        raise fatal[0]
