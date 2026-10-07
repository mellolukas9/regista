"""What this machine allows, decided locally (docs/specs/agent.md, "Operação").

Two controls that depend on nobody but the people who look after the machine:

* the **list of allowed robots** (`allowed_bots` in `agent.toml`). Empty means no robot runs: a
  robot is released with `regista-agent allow <pacote>`, as an administrator, on the machine;
* the **kill switch**: while the file `PAUSED` is in the agent's folder, the agent asks for no
  new run (the one in progress is not touched, and the heartbeat goes on).

The panel can neither change the list nor pause or resume: the server only learns that the switch
is on (an indication, "Pausada nesta máquina"). Both are changed by the CLI, which asks for an
elevated console, because a person who can write in the agent's folder can already do anything.
"""

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from regista_agent.config import CONFIG_NAME, AgentSettings
from regista_agent.errors import AgentError

PAUSE_FILE = "PAUSED"
_PACKAGE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def pause_path(settings: AgentSettings) -> Path:
    return settings.home / PAUSE_FILE


def is_paused(settings: AgentSettings) -> bool:
    return pause_path(settings).exists()


def is_allowed(settings: AgentSettings, package_name: str) -> bool:
    return package_name in settings.allowed_bots


def is_elevated() -> bool:
    """Administrator on Windows, root elsewhere."""
    if sys.platform == "win32":
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return os.geteuid() == 0


def require_elevation(settings: AgentSettings, what: str) -> None:
    """These commands change what the machine allows; in development nobody is an administrator."""
    if settings.environment == "dev" or is_elevated():
        return
    raise AgentError(f"{what} exige um console aberto como Administrador.")


def _read(settings: AgentSettings) -> dict[str, Any]:
    path = settings.home / CONFIG_NAME
    return tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}


def _write(settings: AgentSettings, data: dict[str, Any]) -> None:
    path = settings.home / CONFIG_NAME
    settings.home.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.new")
    temporary.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(temporary, path)


def _checked(package_name: str) -> str:
    if not _PACKAGE_NAME.fullmatch(package_name):
        raise AgentError(
            f"'{package_name}' não é um nome de pacote (letras minúsculas, números e _, "
            "começando por letra)."
        )
    return package_name


def allow(settings: AgentSettings, package_name: str) -> list[str]:
    data = _read(settings)
    allowed = sorted({*data.get("allowed_bots", []), _checked(package_name)})
    _write(settings, {**data, "allowed_bots": allowed})
    return allowed


def disallow(settings: AgentSettings, package_name: str) -> list[str]:
    data = _read(settings)
    allowed = sorted(set(data.get("allowed_bots", [])) - {_checked(package_name)})
    _write(settings, {**data, "allowed_bots": allowed})
    return allowed


def pause(settings: AgentSettings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    pause_path(settings).write_text("paused\n", encoding="utf-8")


def resume(settings: AgentSettings) -> None:
    pause_path(settings).unlink(missing_ok=True)
