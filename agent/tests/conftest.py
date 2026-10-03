"""Shared fixtures of the agent tests."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


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
