"""Helpers: tiny wheels in a local folder, so builds run offline and fast."""

import json
import zipfile
from pathlib import Path


def make_wheel(
    folder: Path,
    name: str,
    version: str = "1.0",
    *,
    requires: tuple[str, ...] = (),
    tag: str = "py3-none-any",
    files: dict[str, bytes] | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dist = f"{name}-{version}"
    path = folder / f"{dist}-{tag}.whl"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {r}\n" for r in requires
    )
    wheel = f"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: {tag}\n"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name}/__init__.py", b"")
        for member, content in (files or {}).items():
            zf.writestr(member, content)
        zf.writestr(f"{dist}.dist-info/METADATA", metadata)
        zf.writestr(f"{dist}.dist-info/WHEEL", wheel)
        zf.writestr(f"{dist}.dist-info/RECORD", "")
    return path


def make_playwright_wheel(folder: Path, version: str = "1.55.0", revision: str = "1187") -> Path:
    browsers = json.dumps({"browsers": [{"name": "chromium", "revision": revision}]}).encode()
    return make_wheel(
        folder,
        "playwright",
        version,
        tag="py3-none-win_amd64",
        files={"playwright/driver/package/browsers.json": browsers},
    )


def make_robot(parent: Path, name: str = "demo_robot", requirements: str | None = None) -> Path:
    robot = parent / name
    robot.mkdir(parents=True)
    (robot / "main.py").write_text("print('hello from the robot')\n", encoding="utf-8")
    if requirements is not None:
        (robot / "requirements.txt").write_text(requirements, encoding="utf-8")
    return robot
