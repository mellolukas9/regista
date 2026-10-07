"""The environment of a package version, made with uv and never with the network (ADR 0021).

`uv venv` from the exact Python the manifest names, then `uv pip install` from the `wheels/` and
the hashed `requirements.lock` that came inside the signed package: `--offline --no-index
--find-links wheels --require-hashes`. The agent's own uv cache is used and nothing is downloaded,
so a machine behind a proxy that blocks the PyPI runs the same as any other.

An environment is reused while its marker is there. A robot runs with the permissions of the
account that runs the agent, so a compromised robot could change the environment it runs in (and
the next run would use it): accepted for the MVP and recorded in docs/STATUS.md.
"""

import logging
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

import uv

from regista_agent.config import AgentSettings

log = logging.getLogger("regista_agent")

MARKER = ".regista-env-ok"
Runner = Callable[[list[str], Mapping[str, str]], "subprocess.CompletedProcess[str]"]


class EnvironmentFailed(Exception):
    """The environment could not be made. The detail is for the logs of the run."""


def default_runner(args: list[str], env: Mapping[str, str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603  (fixed argument lists, no shell)
        args, env=dict(env), capture_output=True, text=True, check=False, errors="replace"
    )


def uv_env(settings: AgentSettings) -> dict[str, str]:
    """A small environment for uv: its own cache, no downloads, no index, no project files."""
    base = {k: v for k, v in os.environ.items() if k in _KEEP or k.upper() in _KEEP}
    base.update(
        {
            "UV_CACHE_DIR": str(settings.uv_cache_dir),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        }
    )
    return base


_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME")


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def env_path(settings: AgentSettings, sha256: str) -> Path:
    return settings.envs_dir / sha256


def ensure(
    settings: AgentSettings,
    *,
    sha256: str,
    base_python: Path,
    wheels: Path,
    lock: Path,
    runner: Runner | None = None,
) -> Path:
    """The Python of the environment of this package, made if it is not there yet."""
    runner = runner or default_runner  # looked up now, so a test can stand in for it
    final = env_path(settings, sha256)
    if (final / MARKER).is_file() and venv_python(final).is_file():
        return venv_python(final)
    shutil.rmtree(final, ignore_errors=True)  # a half-made one from a crash

    settings.envs_dir.mkdir(parents=True, exist_ok=True)
    building = settings.envs_dir / f"{sha256}.{uuid.uuid4().hex[:8]}.tmp"
    env = uv_env(settings)
    try:
        _run(runner, [uv.find_uv_bin(), "venv", "--python", str(base_python), str(building)], env)
        if lock.is_file() and lock.read_text("utf-8").strip():
            _run(
                runner,
                [
                    uv.find_uv_bin(), "pip", "install", "--python", str(venv_python(building)),
                    "--offline", "--no-index", "--find-links", str(wheels), "--require-hashes",
                    "--requirement", str(lock),
                ],
                env,
            )  # fmt: skip
        (building / MARKER).write_text(sha256, encoding="utf-8")
        os.replace(building, final)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return venv_python(final)


def _run(runner: Runner, args: list[str], env: Mapping[str, str]) -> None:
    try:
        result = runner(args, env)
    except OSError as exc:
        raise EnvironmentFailed(f"{args[1]}: {exc}") from exc
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        raise EnvironmentFailed(f"uv {args[1]} falhou ({result.returncode}): {output[-800:]}")


def prune(settings: AgentSettings, *, live: set[str]) -> None:
    """Remove the environments of packages that are no longer in the cache."""
    if not settings.envs_dir.is_dir():
        return
    for entry in settings.envs_dir.iterdir():
        name = entry.name.split(".")[0]
        if name in live and not entry.name.endswith(".tmp"):
            continue
        shutil.rmtree(entry, ignore_errors=True)
