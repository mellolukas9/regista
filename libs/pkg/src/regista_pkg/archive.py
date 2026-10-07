"""Reading and extracting a package without trusting it.

A package is a zip that has already been verified by hash and signature, but extraction still
treats every entry as hostile: a signed package is only as good as the person who signed it, and
the same code reads packages on the server (to check the inner manifest) and on the machines.

Layout of a package:

    manifest.json        the inner manifest (what the signature vouches for, minus hash and size)
    requirements.lock    pinned requirements with hashes (what `uv pip install` installs)
    bot/...              the robot's code; `bot/main.py` is the entry point
    wheels/*.whl         every dependency, already downloaded
"""

import re
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from regista_pkg.manifest import Manifest
from regista_pkg.reasons import PackageError

MANIFEST_NAME = "manifest.json"
LOCK_NAME = "requirements.lock"
BOT_DIR = "bot"
WHEELS_DIR = "wheels"
ENTRY_POINT = "bot/main.py"

MAX_MEMBERS = 20_000
MAX_NAME_LENGTH = 240
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16_384
# A file that expands more than this is a bomb, whatever its size (small files may compress hard).
MAX_RATIO = 1000
_RATIO_FLOOR_BYTES = 1024 * 1024
_COPY_CHUNK = 1024 * 1024

_TOP_LEVEL_FILES = {MANIFEST_NAME, LOCK_NAME}
_TOP_LEVEL_DIRS = {BOT_DIR, WHEELS_DIR}
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}  # fmt: skip
_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


def _unsafe(detail: str) -> PackageError:
    return PackageError("unsafe_archive", detail)


def _check_name(name: str) -> PurePosixPath:
    """A name that is safe on Windows and elsewhere, or `unsafe_archive`."""
    if not name or len(name) > MAX_NAME_LENGTH:
        raise _unsafe("name length")
    if "\\" in name or ":" in name or _CONTROL.search(name) or _DRIVE.match(name):
        raise _unsafe(f"name characters: {name!r}")
    if name.startswith("/"):
        raise _unsafe(f"absolute path: {name!r}")
    # Split the raw name: PurePosixPath would quietly drop "//" and "." and hide them.
    parts = name.rstrip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise _unsafe(f"path traversal: {name!r}")
        # Windows drops trailing dots and spaces, so `evil.` would be `evil`.
        if part != part.rstrip(". "):
            raise _unsafe(f"trailing dot or space: {name!r}")
        if part.split(".")[0].lower() in _RESERVED:
            raise _unsafe(f"reserved device name: {name!r}")
    return PurePosixPath(*parts)


def validate_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Every member, checked. Raises `unsafe_archive` or `too_large`; returns the file members."""
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise PackageError("too_large", f"{len(infos)} members")
    kinds: dict[str, str] = {}  # lowercase path -> "dir" or "file"
    total = 0
    files: list[zipfile.ZipInfo] = []
    for info in infos:
        path = _check_name(info.filename)
        is_dir = info.is_dir()
        # Zip tools that set no file type leave only permission bits; a type that is there must
        # be a regular file or a folder.
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR):
            raise _unsafe(f"not a regular file: {info.filename!r}")  # symlinks, devices
        if info.flag_bits & 0x1:
            raise _unsafe(f"encrypted member: {info.filename!r}")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise _unsafe(f"compression method: {info.filename!r}")

        top = path.parts[0]
        if len(path.parts) == 1 and not is_dir:
            if top not in _TOP_LEVEL_FILES:
                raise _unsafe(f"unexpected file: {info.filename!r}")
        elif top not in _TOP_LEVEL_DIRS:
            raise _unsafe(f"unexpected folder: {info.filename!r}")

        # Two names that differ only by case are the same file on Windows, and a name cannot be
        # both a file and a folder.
        for depth in range(1, len(path.parts)):
            parent = "/".join(path.parts[:depth]).lower()
            if kinds.setdefault(parent, "dir") != "dir":
                raise _unsafe(f"name used as file and folder: {info.filename!r}")
        key = path.as_posix().lower()
        kind = "dir" if is_dir else "file"
        if key in kinds and not (is_dir and kinds[key] == "dir"):
            raise _unsafe(f"name used twice: {info.filename!r}")
        kinds[key] = kind

        if not is_dir:
            if (
                info.file_size > _RATIO_FLOOR_BYTES
                and info.file_size > max(info.compress_size, 1) * MAX_RATIO
            ):
                raise PackageError("too_large", f"compression ratio: {info.filename!r}")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise PackageError("too_large", "uncompressed size")
            files.append(info)
    return files


def read_inner_manifest(zf: zipfile.ZipFile) -> Manifest:
    """`manifest.json`, parsed strictly. Its absence or a bad one is `malformed_package`."""
    try:
        info = zf.getinfo(MANIFEST_NAME)
    except KeyError:
        raise PackageError("malformed_package", "no manifest.json") from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise PackageError("malformed_package", "manifest.json too large")
    with zf.open(info) as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    return Manifest.from_json(raw)


def open_package(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError("malformed_package", f"not a zip: {exc}") from None


def require_layout(zf: zipfile.ZipFile) -> None:
    """The members every package must have."""
    names = set(zf.namelist())
    for needed in (MANIFEST_NAME, LOCK_NAME, ENTRY_POINT):
        if needed not in names:
            raise PackageError("malformed_package", f"missing {needed}")


def safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    """Extract into an empty `destination`, member by member, never through `extractall`.

    Every member is validated first. Each file is then written under a resolved path that must
    stay inside `destination`, and the bytes actually copied are counted against the size the
    entry declared (a header that lies about its size is a bomb, not a package).
    """
    files = validate_members(zf)
    require_layout(zf)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise _unsafe("destination is not empty")
    root = destination.resolve()
    for info in files:
        target = (root / info.filename).resolve()
        if not target.is_relative_to(root):
            raise _unsafe(f"outside the destination: {info.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with zf.open(info) as source, target.open("xb") as out:
                while chunk := source.read(_COPY_CHUNK):
                    written += len(chunk)
                    if written > info.file_size:
                        raise PackageError(
                            "too_large", f"{info.filename!r} is bigger than declared"
                        )
                    out.write(chunk)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            raise PackageError("malformed_package", f"{info.filename!r}: {exc}") from None
        if written != info.file_size:
            raise PackageError("malformed_package", f"{info.filename!r} is shorter than declared")
