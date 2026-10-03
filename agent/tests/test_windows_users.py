"""Enrolled by an administrator, read by the account that runs the agent (Windows, docs/adr/0018).

`enroll` runs in an administrator's console, while the service runs as another account. This test
proves the key is readable by exactly that other account: it creates two temporary local users,
enrolls with one of them as the agent account, then reads the key as each of them.

It needs an elevated session (to create users and to write in the locked-down folder), which the
CI Windows runner has; anywhere else it is skipped with the reason.
"""

import ctypes
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import pytest

from regista_agent import enroll as enroll_module
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError
from regista_agent.keystore import KeyStore

from .support import KEY, SERVER, EnrollServer

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only (local users and ACLs)"),
    pytest.mark.skipif(
        sys.platform != "win32" or not ctypes.windll.shell32.IsUserAnAdmin(),  # type: ignore[attr-defined,unused-ignore]
        reason="needs an elevated session to create local users",
    ),
]

_LOGON32_LOGON_NETWORK = 3
_LOGON32_PROVIDER_DEFAULT = 0


def _net(*args: str) -> None:
    subprocess.run(["net", *args], check=True, capture_output=True)  # noqa: S603, S607


@contextmanager
def _local_user(name: str, password: str) -> Iterator[None]:
    _net("user", name, password, "/add")
    try:
        yield
    finally:
        _net("user", name, "/delete")


@contextmanager
def _as(user: str, password: str) -> Iterator[None]:
    """Run the block with this thread acting as `user`, so file access is judged as theirs."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.LogonUserW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.LogonUserW.restype = wintypes.BOOL
    advapi32.ImpersonateLoggedOnUser.argtypes = [wintypes.HANDLE]
    advapi32.ImpersonateLoggedOnUser.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi32.LogonUserW(
        user, ".", password, _LOGON32_LOGON_NETWORK, _LOGON32_PROVIDER_DEFAULT, ctypes.byref(token)
    ):
        raise OSError(f"LogonUser failed for {user} (error {ctypes.get_last_error()})")
    try:
        if not advapi32.ImpersonateLoggedOnUser(token):
            raise OSError(f"ImpersonateLoggedOnUser failed ({ctypes.get_last_error()})")
        try:
            yield
        finally:
            advapi32.RevertToSelf()
    finally:
        kernel32.CloseHandle(token)


def test_enrolled_by_an_administrator_and_read_by_the_configured_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from regista_agent import _windows

    for name in list(os.environ):
        if name.startswith("REGISTA_"):
            monkeypatch.delenv(name)
    # ProgramData lets every user walk in, like the real home; a temp folder inside someone's
    # profile would not, and the test would fail for the wrong reason.
    home = Path(os.environ["ProgramData"]) / f"regista-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("REGISTA_HOME", str(home))
    suffix = uuid.uuid4().hex[:6]
    agent_user, other_user = f"rgagent{suffix}", f"rgother{suffix}"
    password = secrets.token_urlsafe(18) + "aA1!"

    try:
        with _local_user(agent_user, password), _local_user(other_user, password):
            computer = os.environ["COMPUTERNAME"]
            server = EnrollServer(mode="session")
            result = enroll_module.enroll(
                AgentSettings(),
                url=SERVER,
                key=KEY,
                agent_account=f"{computer}\\{agent_user}",
                http=server.session(),
            )
            assert result.agent_account == f"{computer}\\{agent_user}"

            settings = AgentSettings()
            store = KeyStore(settings.keys_dir, agent_account=result.agent_account)
            assert store.check_acl().ok, store.check_acl().problems

            # An administrator (who ran enroll) still reads it: that is how administrators work.
            as_admin = store.load()

            # The configured account reads exactly the same key.
            with _as(agent_user, password):
                as_agent = store.load()
            assert as_agent.private_bytes_raw() == as_admin.private_bytes_raw()

            # Another ordinary user of the same computer cannot even open the file.
            with _as(other_user, password), pytest.raises(AgentError, match="ler a chave"):
                store.load()

            # The ACL names the three accounts and no one else: not the person who ran enroll.
            dacl = _windows.read_dacl(settings.keys_dir)
            principals = {a.sid for a in dacl.aces if a.kind == "A"}
            assert principals == {
                _windows.SYSTEM_SID,
                _windows.ADMINISTRATORS_SID,
                _windows.resolve_sid(f"{computer}\\{agent_user}"),
            }
    finally:
        keys = home / "keys"
        if keys.exists():
            # Administrators keep full control, which is enough to delete the folder.
            _windows.apply_dacl(keys, "D:(A;OICI;FA;;;BA)")
        shutil.rmtree(home, ignore_errors=True)
