"""Execution modes (docs/specs/agent.md). The agent's core is the same for all of them; what is
specific to how the process is started lives in one module per mode.

- `service`: runs in the background (a Windows service or systemd unit), on any session.
- `session`: runs on the desktop of a dedicated logged-in user, for robots that need a window.
- `oneshot`: takes one job, runs it and exits. Prepared, outside the MVP.
"""

import sys

from regista_agent.errors import AgentError

MODES = ("service", "session", "oneshot")


def is_interactive_session() -> bool:
    """Is this process on a user's desktop? On Windows, services live in session 0, which has
    none. Elsewhere there is no such distinction to make."""
    if sys.platform == "win32":
        from regista_agent import _windows

        return _windows.current_session_id() != 0
    return True


def preflight(mode: str) -> None:
    """Refuse to start in a place where the mode cannot work, with a message that says why."""
    from regista_agent.modes import oneshot, service, session

    checks = {
        "service": service.preflight,
        "session": session.preflight,
        "oneshot": oneshot.preflight,
    }
    if mode not in checks:
        raise AgentError(f"Modo desconhecido: {mode}.")
    checks[mode]()
