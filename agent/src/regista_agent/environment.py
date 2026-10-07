"""The environment of one run, made with uv and never with the network (ADR 0021, ADR 0022).

Every run gets a **new** virtual environment, made by the agent (not by the robot) in the run's own
folder from the wheels and the hashed `requirements.lock` of the signed package that was just
verified: `uv venv` from the exact Python the manifest names, then `uv pip install --offline
--no-index --find-links wheels --require-hashes --no-cache --link-mode=copy`.

* Nothing a robot writes is ever reused: the folder goes away when the run ends, so there is no
  environment to poison for the next run (it used to persist, ADR 0022).
* `--no-cache`: uv does not recheck the hash of what it takes from its cache, so a cache entry
  changed by anyone would land in the new environment unnoticed (measured in the M4b spike).
* `--link-mode=copy`: a hardlink would share the file, and with it the ACL, of the cache.
* The robot cannot reach `uv-cache`, `packages` or anything else of the agent's: it only reads
  this environment and the code of its own run (docs/specs/security.md).
"""

import logging
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import uv

from regista_agent.config import AgentSettings

log = logging.getLogger("regista_agent")

Runner = Callable[[list[str], Mapping[str, str]], "subprocess.CompletedProcess[str]"]


class EnvironmentFailed(Exception):
    """The environment could not be made. The detail is for the logs of the run."""


def default_runner(args: list[str], env: Mapping[str, str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603  (fixed argument lists, no shell)
        args, env=dict(env), capture_output=True, text=True, check=False, errors="replace"
    )


def uv_env(settings: AgentSettings) -> dict[str, str]:
    """A small environment for uv: its own cache, no downloads, no index, no project files."""
    import os

    base = {k: v for k, v in os.environ.items() if k in _KEEP or k.upper() in _KEEP}
    # What the agent itself uses to reach the server also applies to uv (setup downloads): the
    # proxy and the CA bundle from `agent.toml`, and the standard variables a company sets.
    if settings.proxy:
        base["HTTPS_PROXY"] = base["HTTP_PROXY"] = settings.proxy
    if settings.ca_bundle is not None:
        base["SSL_CERT_FILE"] = str(settings.ca_bundle)
    base.update(
        {
            "UV_CACHE_DIR": str(settings.uv_cache_dir),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        }
    )
    return base


_KEEP = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "UV_NATIVE_TLS", "UV_HTTP_TIMEOUT",
)  # fmt: skip


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def build(
    settings: AgentSettings,
    *,
    base_python: Path,
    wheels: Path,
    lock: Path,
    venv: Path,
    runner: Runner | None = None,
) -> Path:
    """Make the environment in `venv` (an empty folder whose permissions were already written, so
    what lands in it is born with them) and return its Python."""
    runner = runner or default_runner  # looked up now, so a test can stand in for it
    env = uv_env(settings)
    _run(runner, [uv.find_uv_bin(), "venv", "--python", str(base_python), str(venv)], env)
    if lock.is_file() and lock.read_text("utf-8").strip():
        _run(
            runner,
            [
                uv.find_uv_bin(), "pip", "install", "--python", str(venv_python(venv)),
                "--offline", "--no-index", "--find-links", str(wheels), "--require-hashes",
                "--no-cache", "--link-mode=copy", "--requirement", str(lock),
            ],
            env,
        )  # fmt: skip
    return venv_python(venv)


def _run(runner: Runner, args: list[str], env: Mapping[str, str]) -> None:
    try:
        result = runner(args, env)
    except OSError as exc:
        raise EnvironmentFailed(f"{args[1]}: {exc}") from exc
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        raise EnvironmentFailed(f"uv {args[1]} falhou ({result.returncode}): {output[-800:]}")
