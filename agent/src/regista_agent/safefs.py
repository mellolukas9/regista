"""Files and folders a robot may have tampered with (ADR 0022).

A robot writes in its run folder, and the agent (more privileged) later reads and deletes what is
there. That is a confused-deputy situation: the robot can create a junction or a link in
`artifacts\` that points at `keys\machine.key`, and an agent that followed it would upload the
key as a "screenshot". So the agent only reads **ordinary files that are not links**, and deletes
a folder without ever following a link.
"""

import os
import shutil
import stat
import sys
from pathlib import Path


def read_regular_file(path: Path, max_bytes: int) -> bytes | None:
    """The bytes of an ordinary file (not a link, not a junction, not a folder), or None."""
    if sys.platform == "win32":
        from regista_agent import _windows

        return _windows.read_regular_file(path, max_bytes)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return None
        data = stream.read(max_bytes + 1)
    return data if len(data) <= max_bytes else None


def regular_files(folder: Path) -> list[Path]:
    """Names in `folder` that are ordinary files by their own attributes (a link is not followed,
    so it is not one). Sorted. The caller still reads them with `read_regular_file`."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    found: list[Path] = []
    for entry in entries:
        try:
            if (
                not entry.is_symlink()
                and not (sys.platform == "win32" and entry.is_junction())
                and entry.is_file(follow_symlinks=False)
            ):
                found.append(Path(entry.path))
        except OSError:
            continue
    return sorted(found)


def remove_tree(path: Path) -> bool:
    """Delete `path` and what is inside without following links: a link or junction is removed
    as itself, its target is left alone. Returns False when something could not be deleted."""
    if not os.path.lexists(path):
        return True
    clean = True

    def onexc(function: object, target: str, _exc: object) -> None:
        nonlocal clean
        try:  # a read-only file is the usual reason
            os.chmod(target, stat.S_IWRITE)
            os.remove(target)
        except OSError:
            clean = False

    if path.is_symlink() or (sys.platform == "win32" and path.is_junction()):
        try:
            os.rmdir(path) if path.is_dir() else os.remove(path)
        except OSError:
            return False
        return True
    shutil.rmtree(path, onexc=onexc)  # junctions inside are removed, not entered (3.8+)
    return clean and not os.path.lexists(path)
