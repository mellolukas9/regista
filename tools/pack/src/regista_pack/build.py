"""Building a package: lock the dependencies, download their wheels, zip, hash and sign.

Everything the robot needs goes inside the package, so the machine that runs it never reaches the
PyPI (ADR 0021). Only wheels for Windows 64 bits (`win_amd64`) and the exact Python the manifest
names are included; a dependency that has only source code makes the build fail, because compiling
it on a customer's machine would run code nobody signed.
"""

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import uv
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_pack.keys import PackError
from regista_pkg import Manifest, PackageError, sign
from regista_pkg import archive as arch

PLATFORM = "win_amd64"
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
_PACKAGE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_EXACT_PYTHON = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # the same bytes every time the same input is built
_SKIP_DIRS = {"__pycache__", ".git", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}
_NO_WHEEL = (
    re.compile(r"Because (?P<name>[A-Za-z0-9_.\-]+)(?:==\S+)? has no usable wheels"),
    re.compile(r"No matching distribution found for (?P<name>[A-Za-z0-9_.\-]+)"),
    re.compile(
        r"Could not find a version that satisfies the requirement (?P<name>[A-Za-z0-9_.\-]+)"
    ),
    re.compile(r"(?P<name>[A-Za-z0-9_.\-]+) was not found in the package registry"),
)


@dataclass(frozen=True)
class BuiltPackage:
    client: str
    package_path: Path
    signature_path: Path
    sha256: str
    size: int


def _run(args: list[str], what: str) -> str:
    result = subprocess.run(  # noqa: S603  (fixed argument lists, no shell)
        args, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise PackError(_explain_failure(output, what))
    return output


def _explain_failure(output: str, what: str) -> str:
    for pattern in _NO_WHEEL:
        found = pattern.search(output)
        if found:
            name = found.group("name")
            return (
                f"A dependência '{name}' não tem wheel para Windows 64 bits ({PLATFORM}) com este "
                "Python. O regista-pack não inclui código-fonte (sdist), porque compilar na "
                "máquina do cliente executaria código que ninguém assinou. Use uma versão que "
                "publique "
                f"wheel para Windows ou tire a dependência.\n\nSaída de {what}:\n{output.strip()}"
            )
    return f"{what} falhou:\n{output.strip()}"


def _uv() -> str:
    return uv.find_uv_bin()


def python_of(robot_dir: Path, option: str | None) -> str:
    """The exact Python (X.Y.Z) the package asks for: the option, else `.python-version`."""
    value = option
    if value is None:
        marker = robot_dir / ".python-version"
        value = marker.read_text("utf-8").strip() if marker.is_file() else None
    if value is None or not _EXACT_PYTHON.fullmatch(value):
        raise PackError(
            "Diga o Python exato do robô (X.Y.Z, por exemplo 3.13.5) com --python ou em "
            "`.python-version` dentro da pasta do robô. Versões como 3.13 não valem: o agente "
            "só roda o runtime exato que o pacote declara."
        )
    return value


def lock_requirements(
    requirements: Path, lock: Path, *, python: str, find_links: Path | None, no_index: bool
) -> None:
    """Pin every dependency (and the dependencies of those) with hashes, for Windows 64 bits."""
    minor = ".".join(python.split(".")[:2])
    command = [
        _uv(), "pip", "compile", str(requirements), "--output-file", str(lock),
        "--generate-hashes", "--no-header", "--python-version", minor,
        "--python-platform", "windows", "--only-binary", ":all:", "--quiet",
    ]  # fmt: skip
    if find_links:
        command += ["--find-links", str(find_links)]
    if no_index:
        command += ["--no-index"]
    _run(command, "uv pip compile")


def download_wheels(
    lock: Path, wheels: Path, *, python: str, find_links: Path | None, no_index: bool
) -> None:
    """Fetch exactly the files the lock pins (hashes checked) into `wheels`."""
    minor = ".".join(python.split(".")[:2])
    command = [
        sys.executable, "-m", "pip", "download", "--requirement", str(lock),
        "--dest", str(wheels), "--only-binary=:all:", "--no-deps", "--require-hashes",
        "--platform", PLATFORM, "--python-version", minor, "--implementation", "cp",
        "--abi", f"cp{minor.replace('.', '')}", "--disable-pip-version-check", "--quiet",
    ]  # fmt: skip
    if find_links:
        command += ["--find-links", str(find_links)]
    if no_index:
        command += ["--no-index"]
    _run(command, "pip download")


def _locked(lock: Path, name: str) -> str | None:
    """The pinned version of `name` in the lock, or None."""
    pattern = re.compile(rf"^{re.escape(name)}==([0-9][^\s;\\]*)", re.IGNORECASE | re.MULTILINE)
    found = pattern.search(lock.read_text("utf-8"))
    return found.group(1) if found else None


def chromium_revision(wheels: Path) -> str:
    """The Chromium revision the Playwright wheel in `wheels` expects (from its browsers.json)."""
    for wheel in sorted(wheels.glob("playwright-*.whl")):
        with zipfile.ZipFile(wheel) as zf:
            try:
                data = json.loads(zf.read("playwright/driver/package/browsers.json"))
            except KeyError:
                continue
        for browser in data.get("browsers", []):
            if browser.get("name") == "chromium":
                return str(browser["revision"])
    raise PackError(
        "Não achei a revisão do Chromium na wheel do Playwright. O pacote precisa declará-la."
    )


def _members(robot_dir: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in sorted(robot_dir.rglob("*")):
        relative = path.relative_to(robot_dir)
        if any(part in _SKIP_DIRS for part in relative.parts) or path.suffix in _SKIP_SUFFIXES:
            continue
        if path.is_symlink():
            raise PackError(f"{relative} é um link simbólico; o pacote só leva arquivos comuns.")
        if path.is_file():
            found.append((f"{arch.BOT_DIR}/{relative.as_posix()}", path))
    return found


def _write_zip(target: Path, manifest: Manifest, lock: Path, wheels: Path, robot: Path) -> None:
    entries: list[tuple[str, bytes | Path]] = [
        (arch.MANIFEST_NAME, manifest.to_json()),
        (arch.LOCK_NAME, lock.read_bytes()),
    ]
    entries += [(name, path) for name, path in _members(robot)]
    entries += [(f"{arch.WHEELS_DIR}/{w.name}", w) for w in sorted(wheels.glob("*.whl"))]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, content if isinstance(content, bytes) else content.read_bytes())


def build(
    robot_dir: Path,
    *,
    version: str,
    clients: list[str],
    key: Ed25519PrivateKey,
    key_id: str,
    out_dir: Path,
    python: str | None = None,
    find_links: Path | None = None,
    no_index: bool = False,
) -> list[BuiltPackage]:
    """One signed package per client, all with the same wheels."""
    robot_dir = robot_dir.resolve()
    package_name = robot_dir.name
    if not _PACKAGE_NAME.fullmatch(package_name):
        raise PackError(
            f"O nome da pasta do robô ('{package_name}') é o nome do pacote: use letras "
            "minúsculas, números e _, começando por letra."
        )
    if not (robot_dir / "main.py").is_file():
        raise PackError(f"Não achei main.py em {robot_dir}.")
    if not clients:
        raise PackError("Diga para qual cliente é o pacote com --client <id> (pode repetir).")
    canonical: list[str] = []
    for client in clients:
        try:
            canonical.append(str(uuid.UUID(client)))
        except ValueError:
            raise PackError(f"'{client}' não é o id de um cliente (um UUID).") from None
    exact_python = python_of(robot_dir, python)
    requirements = robot_dir / "requirements.txt"

    out_dir.mkdir(parents=True, exist_ok=True)
    built: list[BuiltPackage] = []
    with tempfile.TemporaryDirectory(prefix="regista-pack-") as folder:
        work = Path(folder)
        lock, wheels = work / arch.LOCK_NAME, work / "wheels"
        wheels.mkdir()
        if requirements.is_file() and requirements.read_text("utf-8").strip():
            lock_requirements(
                requirements, lock, python=exact_python, find_links=find_links, no_index=no_index
            )
            download_wheels(
                lock, wheels, python=exact_python, find_links=find_links, no_index=no_index
            )
        else:
            lock.write_text("", encoding="utf-8")
        playwright = _locked(lock, "playwright")
        revision = chromium_revision(wheels) if playwright else None

        for client in canonical:
            manifest = Manifest(
                tenant_id=client,
                package_name=package_name,
                version=version,
                python=exact_python,
                playwright=playwright,
                chromium_revision=revision,
            )
            stem = f"{package_name}-{version}-{client[:8]}"
            package_path = out_dir / f"{stem}.rgpkg"
            _write_zip(package_path, manifest, lock, wheels, robot_dir)
            _check_own_work(package_path, manifest)
            data = package_path.read_bytes()
            sha256, size = hashlib.sha256(data).hexdigest(), len(data)
            if size > MAX_PACKAGE_BYTES:
                package_path.unlink()
                raise PackError(
                    f"O pacote tem {size / 1048576:.0f} MB e o limite é "
                    f"{MAX_PACKAGE_BYTES // 1048576} MB. Tire dependências que o robô não usa."
                )
            signature_path = out_dir / f"{stem}.rgsig"
            signature_path.write_bytes(sign(key, manifest, sha256=sha256, size=size, key_id=key_id))
            built.append(BuiltPackage(client, package_path, signature_path, sha256, size))
    return built


def _check_own_work(package_path: Path, manifest: Manifest) -> None:
    """The package must pass the same checks the agent and the server will apply."""
    try:
        with arch.open_package(package_path) as zf:
            arch.validate_members(zf)
            arch.require_layout(zf)
            if arch.read_inner_manifest(zf) != manifest:
                raise PackageError("malformed_package", "manifest differs")
    except PackageError as exc:
        package_path.unlink(missing_ok=True)
        raise PackError(
            f"O pacote gerado seria recusado pelo agente ({exc.reason}: {exc.detail}). Confira "
            "os nomes de arquivo dentro da pasta do robô."
        ) from exc
