"""Shared fixtures of the agent tests."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from .package_support import TestKey, new_key
from .support import TENANT_ID


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty agent home. `REGISTA_*` variables of the developer's own machine are cleared so
    nothing outside the test leaks in."""
    import os

    for name in list(os.environ):
        if name.startswith("REGISTA_"):
            monkeypatch.delenv(name)
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    folder = tmp_path / "regista-home"
    monkeypatch.setenv("REGISTA_HOME", str(folder))
    return folder


@pytest.fixture
def no_acl(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skips locking the key folder down on Windows. For tests about something else (enroll, the
    run loop): they must not need an elevated session. The ACL has tests of its own."""
    if sys.platform == "win32":
        from regista_agent import _windows

        monkeypatch.setattr(_windows, "resolve_sid", lambda account: "S-1-5-21-1-2-3-1000")
        monkeypatch.setattr(_windows, "restrict_directory", lambda directory, sid: None)
    else:
        # The accounts in these tests are made up; elsewhere the key is handed to the account
        # with chown, which would look for a real user of the machine.
        from regista_agent.keystore import KeyStore

        monkeypatch.setattr(KeyStore, "_give_to_agent", lambda self, path: None)
    yield


@pytest.fixture
def key(tmp_path: Path) -> TestKey:
    """A signing key made on the spot, trusted by the test agents through the dev override."""
    return new_key(tmp_path / "keys")


@pytest.fixture
def agent_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home that already knows its client (the identity written at enrollment)."""
    from regista_agent import layout

    # Writing ACLs (also for the run folders) has its own tests; here a production-mode run on a
    # non-elevated Windows console would lock out the very person running the tests.
    monkeypatch.setattr(layout, "lock_applies", lambda settings: False)
    monkeypatch.setattr(layout, "separate_identities", lambda settings: False)
    keys = home / "keys"
    keys.mkdir(parents=True)
    (keys / "identity.json").write_text(json.dumps({"tenant_id": str(TENANT_ID)}), "utf-8")
    return home


@pytest.fixture(autouse=True)
def _agent_home_in_tmp(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may touch the real home of the agent (`/etc/regista`, `%ProgramData%`): runs make a
    folder under it. Tests that care use the `home` fixture, which replaces this."""
    if request.module.__name__.endswith("test_windows_host"):
        return  # that module works on the real home, on purpose (real services)
    monkeypatch.setenv("REGISTA_HOME", str(tmp_path / "auto-home"))
