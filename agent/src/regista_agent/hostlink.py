"""The agent's end of the real pipe to the robot host, with the identity checks (ADR 0022).

`make_launcher` is what `regista-agent run` uses to start robots. On Windows (production) that is
always the host: the pipe is created by the agent (one instance, only the robot's SID may open it),
and each connection is accepted only if the kernel says that whoever connected is who it should be:

* the **SID of the token** the pipe authenticated is the robot account's (never what the other
  side says about itself);
* in `service` mode, the **process id** is the one the service manager reports for the robot
  service (`RegistaRobot`), so a different process with the same identity is not taken for the
  host; in `session` mode the process must be in an interactive session (not session 0);
* when the agent is allowed to look at the process, its **executable** is the agent's own
  interpreter (the agent and the host are installed together). Services of different accounts
  usually cannot look at each other, and then this check is skipped, not failed.

Outside Windows (not in the MVP) and in development with `REGISTA_DEV_DIRECT_ROBOT`, the robot is a
direct child of the agent (`DirectLauncher`).
"""

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from regista_agent import launcher, modes
from regista_agent.config import (
    DEFAULT_SERVICE_ACCOUNT,
    ROBOT_HOST_PIPE,
    ROBOT_SERVICE_NAME,
    AgentSettings,
)
from regista_agent.errors import AgentError

log = logging.getLogger("regista_agent")


@dataclass(frozen=True)
class RobotLink:
    """How robots are started on this machine: the launcher, what to release at the end, and
    whether the robots' session is interactive (the interactive session of the panel)."""

    launcher: launcher.Launcher
    close: Callable[[], None]
    interactive: Callable[[], bool]


def make_launcher(settings: AgentSettings) -> RobotLink:
    """The launcher for this machine."""
    if launcher.direct_allowed(settings):
        log.warning("robots start as children of the agent (no robot host): development only")
        return RobotLink(launcher.DirectLauncher(), lambda: None, modes.is_interactive_session)
    if sys.platform != "win32":  # pragma: no cover  (direct_allowed is true there)
        raise AgentError("O hospedeiro do robô só existe no Windows.")
    return _windows_launcher(settings)


def _windows_launcher(settings: AgentSettings) -> RobotLink:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    from regista_agent import _windows, winpipe

    account = settings.effective_robot_account
    if account is None:
        raise AgentError("A conta do robô não está configurada. Cadastre a máquina (enroll).")
    robot_sid = _windows.resolve_sid(account)
    if robot_sid == _windows.resolve_sid(settings.agent_account or DEFAULT_SERVICE_ACCOUNT):
        raise AgentError("A conta do robô não pode ser a mesma conta do agente.")
    try:
        server = winpipe.PipeServer(ROBOT_HOST_PIPE, [robot_sid])
    except OSError as exc:
        raise AgentError(f"Não foi possível criar o canal do hospedeiro do robô: {exc}") from exc
    session_mode = settings.mode == "session"
    # The image of this very process (in a venv built from an installed Python, `sys.executable`
    # is a launcher and the process is the interpreter behind it).
    my_image = os.path.normcase(
        os.path.realpath(winpipe.process_image(os.getpid()) or sys.executable)
    )

    def accept() -> launcher.Connection | None:
        connection = server.accept(timeout=2.0)  # a timeout is a TimeoutError: just asked again
        refusal = _refusal(connection)
        if refusal is not None:
            log.warning("robot host connection refused: %s", refusal)
            connection.close()
            return None
        return connection

    def _refusal(connection: Any) -> str | None:
        try:
            peer = connection.peer()
        except OSError as exc:
            return f"não foi possível identificar quem conectou ({exc})"
        if peer.sid != robot_sid:
            return f"a conta de quem conectou ({peer.sid}) não é a do robô"
        if session_mode:
            if winpipe.session_of_process(peer.pid) in (None, 0):
                return "no modo Sessão o hospedeiro deveria estar numa sessão interativa"
        elif peer.pid != winpipe.service_process_id(ROBOT_SERVICE_NAME):
            return f"o processo {peer.pid} não é o do serviço {ROBOT_SERVICE_NAME}"
        if peer.image is not None and os.path.normcase(os.path.realpath(peer.image)) != my_image:
            return f"o executável de quem conectou ({peer.image}) não é o esperado"
        return None

    channel = launcher.HostChannel(accept)
    channel.start()

    def close() -> None:
        channel.close()
        server.close()

    def interactive() -> bool:
        # In `session` mode the host is in the dedicated user's desktop session: the machine has
        # an interactive session for robots only while that host is connected.
        return session_mode and channel.connected

    return RobotLink(launcher.HostLauncher(channel), close, interactive)
