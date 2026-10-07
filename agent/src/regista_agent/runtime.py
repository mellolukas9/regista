"""The Python and the Chromium a package asks for, and how a machine gets them (ADR 0021).

A signed manifest names the exact Python (`3.13.5`), the Playwright version and the Chromium
revision it needs. The agent only *checks* for them under `%ProgramData%\\Regista`
(`python\\` and `browsers\\`) and never downloads anything while running a robot. They are
installed by `regista-agent setup`, from an elevated console, in folders that only Administrators
and SYSTEM can write: the account that runs the agent (and so the robot) can read and run them but
not change them, so a compromised robot cannot alter the runtime of the next one.

Integrity of what `setup` downloads: the Python archive is the one uv fetches, and uv checks it
against the hash in its own catalogue. The Chromium that Playwright downloads comes from its CDN
over HTTPS and has no hash we can check: an accepted risk, with the offline MSI of M8 as the way
out (docs/STATUS.md).
"""

import logging
import re
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import uv

from regista_agent import layout
from regista_agent.config import AgentSettings
from regista_agent.environment import Runner, default_runner, uv_env, venv_python
from regista_agent.errors import AgentError
from regista_pkg import Manifest

log = logging.getLogger("regista_agent")


class RuntimeMissing(Exception):
    """This machine has not been prepared for a package. `what` names what is missing."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def python_executable(settings: AgentSettings, version: str) -> Path | None:
    """The interpreter of exactly this Python, if it is installed under the agent's folder."""
    root = settings.python_dir
    if root.is_dir():
        for folder in sorted(root.glob(f"cpython-{version}-*")):
            for candidate in ("python.exe", "bin/python3", "bin/python"):
                path = folder / candidate
                if path.is_file():
                    return path
    return None


def browsers_path(settings: AgentSettings) -> Path:
    """Where the robot finds its browsers. Development may point elsewhere (a dev machine keeps
    its Chromium in the user's profile)."""
    if settings.environment == "dev" and settings.dev_browsers_path is not None:
        return settings.dev_browsers_path
    return settings.browsers_dir


def chromium_installed(settings: AgentSettings, revision: str) -> bool:
    return (browsers_path(settings) / f"chromium-{revision}").is_dir()


def require(settings: AgentSettings, manifest: Manifest) -> Path:
    """The interpreter to build the package's environment from, or `RuntimeMissing`.

    In development a stand-in interpreter (`REGISTA_DEV_PYTHON`) is accepted for the Python; the
    Chromium check applies as always."""
    python: Path | None
    if settings.environment == "dev" and settings.dev_python is not None:
        python = Path(settings.dev_python)
    else:
        python = python_executable(settings, manifest.python)
    if python is None:
        raise RuntimeMissing(f"Python {manifest.python}")
    # A development agent with a stand-in Python and no browsers folder is running a test robot
    # that has no browser to look for.
    standing_in = (
        settings.environment == "dev"
        and settings.dev_python is not None
        and settings.dev_browsers_path is None
    )
    if (
        manifest.chromium_revision is not None
        and not standing_in
        and not chromium_installed(settings, manifest.chromium_revision)
    ):
        raise RuntimeMissing(f"Chromium {manifest.chromium_revision}")
    return python


def installed(settings: AgentSettings) -> tuple[list[str], list[str]]:
    """(Pythons, Chromium revisions) found on this machine, for `diagnose`."""
    pythons = []
    if settings.python_dir.is_dir():
        pythons = sorted(
            p.name.split("-")[1]
            for p in settings.python_dir.glob("cpython-*")
            if re.fullmatch(r"cpython-\d+\.\d+\.\d+-.*", p.name)  # not uv's `3.13` alias
        )
    revisions = []
    base = browsers_path(settings)
    if base.is_dir():
        revisions = sorted(p.name.split("-", 1)[1] for p in base.glob("chromium-*"))
    return pythons, revisions


# --- setup ------------------------------------------------------------------------------------


class Needs(Protocol):
    @property
    def python(self) -> str: ...

    @property
    def playwright(self) -> str | None: ...

    @property
    def chromium_revision(self) -> str | None: ...


@dataclass(frozen=True)
class SimpleNeed:
    """What the command line asks for, when it is not read from the server."""

    python: str
    playwright: str | None = None
    chromium_revision: str | None = None


@dataclass(frozen=True)
class SetupPlan:
    pythons: tuple[str, ...]
    # Playwright version -> (the Python to run it with, the revision it should bring)
    browsers: tuple[tuple[str, str, str | None], ...]


def plan(needs: Iterable[Needs]) -> SetupPlan:
    pythons: set[str] = set()
    browsers: dict[str, tuple[str, str | None]] = {}
    for need in needs:
        pythons.add(need.python)
        if need.playwright is not None:
            browsers[need.playwright] = (need.python, need.chromium_revision)
    return SetupPlan(
        tuple(sorted(pythons)),
        tuple(sorted((v, py, rev) for v, (py, rev) in browsers.items())),
    )


Progress = Callable[[str], None]


def prepare_folders(
    settings: AgentSettings, agent_sid: str | None, robot_sid: str | None = None
) -> list[str]:
    """Create the folders and, on Windows, write the whole permission matrix (`layout.py`): who may
    do what under the agent's home, the runtime (`python`, `browsers`) writable only by
    Administrators and SYSTEM and readable by the agent and the robot, and no access for local
    users. Idempotent: it also makes safe a machine prepared by an older version. Returns what it
    removed from that older state."""
    for folder in (settings.python_dir, settings.browsers_dir):
        folder.mkdir(parents=True, exist_ok=True)
    if not layout.lock_applies(settings):
        return []
    if agent_sid is None:
        raise AgentError("Não achei a conta do agente. Cadastre a máquina antes (enroll).")
    if robot_sid is None:
        raise AgentError("Não achei a conta do robô. Cadastre a máquina antes (enroll).")
    return layout.apply(settings, agent_sid, robot_sid)


def install_python(settings: AgentSettings, version: str, runner: Runner = default_runner) -> Path:
    """`uv python install <version>` into the agent's folder. uv verifies its own download."""
    env = {
        **uv_env(settings),
        "UV_PYTHON_INSTALL_DIR": str(settings.python_dir),
        "UV_PYTHON_DOWNLOADS": "automatic",
        "UV_PYTHON_INSTALL_BIN": "0",
    }
    result = runner([uv.find_uv_bin(), "python", "install", version], env)
    if result.returncode != 0:
        raise AgentError(
            f"Não foi possível instalar o Python {version}: "
            f"{((result.stdout or '') + (result.stderr or '')).strip()[-500:]}"
        )
    found = python_executable(settings, version)
    if found is None:
        raise AgentError(f"O Python {version} não apareceu em {settings.python_dir}.")
    return found


def install_chromium(
    settings: AgentSettings,
    playwright: str,
    python: Path,
    revision: str | None,
    runner: Runner = default_runner,
    wheels: Path | None = None,
) -> None:
    """Install the Chromium that exactly this Playwright version expects, with the Python of the
    package. A throwaway environment runs `playwright install`; browsers land in `browsers\\`."""
    scratch = settings.home / f"setup-{uuid.uuid4().hex[:8]}"
    env = {**uv_env(settings), "UV_PYTHON_DOWNLOADS": "never"}
    try:
        for args in (
            [uv.find_uv_bin(), "venv", "--python", str(python), str(scratch)],
            [
                uv.find_uv_bin(),
                "pip",
                "install",
                "--python",
                str(venv_python(scratch)),
                *_wheel_flags(wheels),
                f"playwright=={playwright}",
            ],
        ):
            result = runner(args, env)
            if result.returncode != 0:
                raise AgentError(f"{args[1]} falhou: {(result.stderr or result.stdout)[-500:]}")
        browsers_env: Mapping[str, str] = {
            **env,
            "PLAYWRIGHT_BROWSERS_PATH": str(settings.browsers_dir),
        }
        result = runner(
            [str(venv_python(scratch)), "-m", "playwright", "install", "chromium"], browsers_env
        )
        if result.returncode != 0:
            raise AgentError(
                f"A instalação do Chromium falhou: {(result.stderr or result.stdout)[-500:]}"
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if revision is not None and not (settings.browsers_dir / f"chromium-{revision}").is_dir():
        raise AgentError(
            f"O Playwright {playwright} não trouxe o Chromium {revision} que o pacote declara."
        )


def _wheel_flags(wheels: Path | None) -> list[str]:
    """With a folder of wheels (the ones inside a signed package, say) nothing is fetched from the
    PyPI: for machines that cannot reach it."""
    if wheels is None:
        return []
    return ["--offline", "--no-index", "--find-links", str(wheels)]


def run_setup(
    settings: AgentSettings,
    needs: Iterable[Needs],
    *,
    agent_sid: str | None,
    robot_sid: str | None = None,
    say: Progress = print,
    runner: Runner = default_runner,
    wheels: Path | None = None,
) -> SetupPlan:
    """Prepare the machine for `needs`: the exact Pythons and Chromium revisions, nothing else."""
    chosen = plan(needs)
    for removed in prepare_folders(settings, agent_sid, robot_sid):
        say(f"Removido do que as versões anteriores deixaram: {removed}.")
    pythons: dict[str, Path] = {}
    for version in chosen.pythons:
        existing = python_executable(settings, version)
        say(f"Python {version}: " + ("já instalado." if existing else "instalando..."))
        pythons[version] = existing or install_python(settings, version, runner)
    for playwright, py_version, revision in chosen.browsers:
        if revision is not None and (settings.browsers_dir / f"chromium-{revision}").is_dir():
            say(f"Chromium {revision}: já instalado.")
            continue
        say(f"Chromium do Playwright {playwright}: instalando...")
        install_chromium(settings, playwright, pythons[py_version], revision, runner, wheels)
    return chosen
