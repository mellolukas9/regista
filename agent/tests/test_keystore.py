"""The machine key at rest: DPAPI and ACLs on Windows, file modes elsewhere (docs/adr/0018)."""

import ctypes
import getpass
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent.errors import AgentError, NotEnrolled
from regista_agent.keystore import KEY_ENTROPY, KeyStore

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only (DPAPI and ACLs)")


def _raw(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )


def _is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0
    return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined,unused-ignore]


def _current_account() -> str:
    if sys.platform == "win32":
        return f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}"
    return getpass.getuser()


@pytest.fixture(autouse=True)
def _unlock_the_key_folder(tmp_path: Path) -> Iterator[None]:
    """A folder these tests lock down leaves even its owner unable to delete it, which makes
    pytest warn and pile up garbage in the temp folder. Give the person access back at the end."""
    yield
    folder = tmp_path / "keys"
    if sys.platform == "win32" and folder.exists():
        from regista_agent import _windows

        me = _windows.resolve_sid(_current_account())
        _windows.apply_dacl(folder, f"D:(A;OICI;FA;;;{me})")


needs_admin = pytest.mark.skipif(
    not _is_admin(),
    reason="needs an elevated session: only an administrator can write in the locked-down folder",
)


# --- any system -------------------------------------------------------------------------------


def test_an_account_that_may_not_look_inside_gets_a_message_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service running as an account the folder does not allow: `is_file` itself raises
    PermissionError on Windows, and the person must see "cannot read the key", not a traceback."""
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())

    def denied(self: Path) -> bool:
        raise PermissionError(5, "Acesso negado", str(self))

    monkeypatch.setattr(Path, "is_file", denied)
    assert store.exists()  # counted as there: it may well be
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(5, "Acesso negado"))
    )
    with pytest.raises(AgentError, match="ler a chave"):
        store.load()


def test_loading_before_enrolling_says_so(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())
    assert not store.exists()
    with pytest.raises(NotEnrolled):
        store.load()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_posix_key_is_private_to_its_owner(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())
    key = Ed25519PrivateKey.generate()
    staged = store.stage(key)
    store.commit(staged)

    assert stat.S_IMODE(store.keys_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert _raw(store.load()) == _raw(key)
    assert store.check_acl().ok


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_posix_check_reports_a_key_that_others_can_read(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())
    store.commit(store.stage(Ed25519PrivateKey.generate()))
    store.path.chmod(0o644)
    report = store.check_acl()
    assert not report.ok and "aberto demais" in report.problems[0]


# --- Windows: DPAPI ---------------------------------------------------------------------------


@windows_only
def test_dpapi_round_trip_needs_the_same_entropy() -> None:
    from regista_agent import _windows

    secret = os.urandom(32)
    blob = _windows.protect(secret, KEY_ENTROPY)
    assert secret not in blob and blob != secret
    assert _windows.unprotect(blob, KEY_ENTROPY) == secret
    # The extra entropy is part of the key: without it (or with another) the blob stays shut.
    with pytest.raises(OSError, match="CryptUnprotectData"):
        _windows.unprotect(blob, b"another entropy")
    with pytest.raises(OSError, match="CryptUnprotectData"):
        _windows.unprotect(blob, b"")


@windows_only
def test_the_file_on_disk_is_not_the_raw_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from regista_agent import _windows

    # This test is about the encryption, not the ACL, so it runs without elevation too.
    monkeypatch.setattr(_windows, "restrict_directory", lambda directory, sid: None)
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())
    key = Ed25519PrivateKey.generate()
    store.commit(store.stage(key))

    on_disk = store.path.read_bytes()
    assert _raw(key) not in on_disk
    assert len(on_disk) > 32
    assert _raw(store.load()) == _raw(key)

    store.path.write_bytes(on_disk[:-4] + b"\x00\x00\x00\x00")  # damaged
    with pytest.raises(AgentError):
        store.load()


# --- Windows: SIDs and ACLs -------------------------------------------------------------------


@windows_only
def test_virtual_service_account_sid_follows_from_its_name() -> None:
    from regista_agent import _windows

    # Known value: the SID Windows gives to `NT SERVICE\TrustedInstaller`.
    assert _windows.service_sid("TrustedInstaller") == (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )
    assert _windows.service_sid("trustedinstaller") == _windows.service_sid("TrustedInstaller")
    assert _windows.resolve_sid(r"NT SERVICE\TrustedInstaller") == _windows.service_sid(
        "TrustedInstaller"
    )
    assert _windows.resolve_sid("SYSTEM").startswith("S-1-5-18")
    with pytest.raises(OSError, match="Conta não encontrada"):
        _windows.resolve_sid(r"NINGUEM\naoexiste")


@windows_only
def test_sddl_parsing_understands_aliases_and_rights() -> None:
    from regista_agent import _windows

    dacl = _windows.parse_sddl(
        "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)(D;;FW;;;S-1-5-80-1)"
    )
    assert dacl.protected
    assert [(a.kind, a.sid) for a in dacl.aces] == [
        ("A", "S-1-5-18"),
        ("A", "S-1-5-32-544"),
        ("A", "S-1-5-32-545"),
        ("D", "S-1-5-80-1"),
    ]
    assert dacl.aces[0].can_read and dacl.aces[2].can_read and not dacl.aces[3].can_read
    assert not _windows.parse_sddl("D:AI(A;ID;FA;;;SY)").protected  # inheriting from the parent


@windows_only
def test_sddl_aliases_that_mean_a_different_sid_on_each_computer_are_resolved() -> None:
    """Windows writes the local Administrator account (RID 500) as the alias `LA`. A table of
    fixed aliases cannot know that SID, and the agent's own account was reported as a stranger."""
    from regista_agent import _windows

    administrator = _windows.normalize_sid("LA")
    assert administrator.startswith("S-1-5-21-") and administrator.endswith("-500")
    assert _windows.normalize_sid("S-1-5-18") == "S-1-5-18"  # already a SID
    assert _windows.normalize_sid("BA") == _windows.ADMINISTRATORS_SID  # well known
    assert _windows.normalize_sid("ZZ") == "ZZ"  # unknown stays visible as unexpected

    dacl = _windows.parse_sddl("D:P(A;OICI;FR;;;LA)(A;OICI;FA;;;SY)")
    assert [a.sid for a in dacl.aces] == [administrator, _windows.SYSTEM_SID]


@windows_only
def test_restricting_a_folder_leaves_only_the_three_accounts(tmp_path: Path) -> None:
    from regista_agent import _windows

    folder = tmp_path / "keys"
    folder.mkdir()
    agent_sid = _windows.resolve_sid(r"NT SERVICE\RegistaAgent")
    me = _windows.resolve_sid(_current_account())
    _windows.restrict_directory(folder, agent_sid)

    dacl = _windows.read_dacl(folder)
    assert dacl.protected, "inheritance is still on"
    principals = {a.sid for a in dacl.aces if a.kind == "A"}
    assert principals == {_windows.SYSTEM_SID, _windows.ADMINISTRATORS_SID, agent_sid}
    # Nothing for Users, Authenticated Users, Everyone... and nothing for whoever ran this.
    assert not principals & {"S-1-5-32-545", "S-1-5-11", "S-1-1-0", me}
    agent_ace = next(a for a in dacl.aces if a.sid == agent_sid)
    assert agent_ace.can_read and agent_ace.rights not in ("FA", "GA")  # read, not full control


@windows_only
@needs_admin
def test_a_staged_key_is_born_protected_and_loads_back(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "keys", agent_account=r"NT SERVICE\RegistaAgent")
    key = Ed25519PrivateKey.generate()
    store.commit(store.stage(key))

    assert store.check_acl().ok, store.check_acl().problems
    assert _raw(store.load()) == _raw(key)  # an administrator can still read it


@windows_only
def test_a_console_that_is_not_elevated_is_told_what_to_do_not_given_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from regista_agent import _windows

    def refuse(path: Path, data: bytes) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(_windows, "make_private_file", refuse)
    store = KeyStore(tmp_path / "keys", agent_account=_current_account())
    with pytest.raises(AgentError, match="Administrador"):
        store.stage(Ed25519PrivateKey.generate())


@windows_only
def test_check_fails_when_the_folder_is_opened_up(tmp_path: Path) -> None:
    from regista_agent import _windows

    account = _current_account()
    agent_sid = _windows.resolve_sid(account)
    folder = tmp_path / "keys"
    folder.mkdir()
    _windows.restrict_directory(folder, agent_sid)
    store = KeyStore(folder, agent_account=account)
    base = f"(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;{agent_sid})"

    # As prepared, the folder has nothing wrong with it (the key itself is missing, which is
    # reported separately).
    assert [p for p in store.check_acl().problems if "fora da lista" in p or "herda" in p] == []

    # Open it up on purpose: Users get read access.
    _windows.apply_dacl(folder, f"D:P{base}(A;OICI;FR;;;BU)")
    problems = store.check_acl().problems
    assert any("fora da lista" in p and "S-1-5-32-545" in p for p in problems), problems

    # And inheritance comes back on: ProgramData's rules flow in again.
    _windows.apply_dacl(folder, f"D:{base}")
    assert any("herda" in p for p in store.check_acl().problems)


@windows_only
@needs_admin
def test_check_fails_when_the_agent_account_cannot_read_the_key(tmp_path: Path) -> None:
    from regista_agent import _windows

    folder = tmp_path / "keys"
    folder.mkdir()
    # The folder was prepared for another account than the one in the configuration.
    _windows.restrict_directory(folder, _windows.resolve_sid(r"NT SERVICE\OutroServico"))
    (folder / "machine.key").write_bytes(b"x")
    store = KeyStore(folder, agent_account=r"NT SERVICE\RegistaAgent")
    problems = store.check_acl().problems
    assert any("não consegue ler" in p for p in problems), problems
    assert any("fora da lista" in p for p in problems), problems


@windows_only
def test_check_fails_when_the_agent_account_is_blocked(tmp_path: Path) -> None:
    from regista_agent import _windows

    account = _current_account()
    agent_sid = _windows.resolve_sid(account)
    folder = tmp_path / "keys"
    folder.mkdir()
    _windows.apply_dacl(
        folder,
        f"D:P(D;OICI;FR;;;{agent_sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;{agent_sid})",
    )
    problems = KeyStore(folder, agent_account=account).check_acl().problems
    assert any("bloqueada" in p for p in problems), problems


@windows_only
def test_the_runtime_folders_are_read_only_for_the_agent_account(tmp_path: Path) -> None:
    """A robot runs as the agent's account, so that account must be able to run the Python and the
    browser but never change them (docs/adr/0021)."""
    from regista_agent import _windows

    folder = tmp_path / "python"
    folder.mkdir()
    agent_sid = _windows.resolve_sid(r"NT SERVICE\RegistaAgent")
    _windows.restrict_directory_read_only(folder, agent_sid)

    dacl = _windows.read_dacl(folder)
    assert dacl.protected
    principals = {a.sid for a in dacl.aces if a.kind == "A"}
    assert principals == {_windows.SYSTEM_SID, _windows.ADMINISTRATORS_SID, agent_sid}
    agent_ace = next(a for a in dacl.aces if a.sid == agent_sid)
    assert agent_ace.can_read and not agent_ace.can_write
    for sid in (_windows.SYSTEM_SID, _windows.ADMINISTRATORS_SID):
        assert next(a for a in dacl.aces if a.sid == sid).can_write
