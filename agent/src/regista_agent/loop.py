"""`regista-agent run`: tell the server "I am alive" every `heartbeat_seconds`.

See docs/specs/agent.md.

One loop for every mode; what differs between modes is only what `modes.preflight` checks first.
The server decides the interval and the agent follows it. Running jobs arrives with M3.
"""

import logging
import threading
from typing import Protocol

from regista_agent import modes, sysinfo
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError, NotEnrolled, ServerUnavailable
from regista_agent.keystore import KeyStore
from regista_agent.transport import AgentSession, HeartbeatInfo, HttpSession, make_client

log = logging.getLogger("regista_agent")

DEFAULT_INTERVAL_SECONDS = 30


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
    modes.preflight(mode)

    store = KeyStore(settings.keys_dir, agent_account=settings.agent_account)
    session = session or build_session(settings, store)
    interval = DEFAULT_INTERVAL_SECONDS
    interactive = modes.is_interactive_session()
    log.info("agent started: machine=%s mode=%s", settings.machine_id, mode)

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
