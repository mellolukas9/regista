"""Windows-only pieces of the key store: DPAPI, SIDs and ACLs (docs/adr/0018).

Only `keystore.py` imports this, and only on Windows. No pywin32 and no `icacls`: `ctypes` calls
the system directly. Accounts are handled as SIDs, never by name, because the names of the
built-in groups are translated (`Administradores`, `Usuários`...) while SIDs are not.

ACLs are written and read as SDDL. That matters for the virtual service account the agent runs
as (`NT SERVICE\\RegistaAgent`): Windows can only map its name to a SID once the service exists,
and `enroll` runs before that. `icacls /grant` refuses a SID it cannot map; an SDDL string does
not, so the ACL can name the account in advance and starts to work when the service appears.
"""

import sys

if sys.platform != "win32":
    raise ImportError("regista_agent._windows only works on Windows")

import ctypes
import hashlib
import os
import re
import struct
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
_SERVICE_PREFIX = "NT SERVICE\\"

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CRYPTPROTECT_LOCAL_MACHINE = 0x4

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SDDL_REVISION_1 = 1

# Well-known SDDL aliases, resolved without asking the system. Anything else (`LA`, the local
# Administrator account; `LG`, `DA`...) is turned into a SID by Windows itself: some aliases mean
# a different SID on every computer, so no fixed table can cover them.
_SDDL_ALIASES = {
    "SY": SYSTEM_SID,
    "BA": ADMINISTRATORS_SID,
    "BU": "S-1-5-32-545",  # Users
    "BG": "S-1-5-32-546",  # Guests
    "AU": "S-1-5-11",  # Authenticated Users
    "WD": "S-1-1-0",  # Everyone
    "IU": "S-1-5-4",  # Interactive
    "LS": "S-1-5-19",  # Local Service
    "NS": "S-1-5-20",  # Network Service
    "CO": "S-1-3-0",  # Creator Owner
    "OW": "S-1-3-4",  # Owner Rights
}
_READ_RIGHTS = {"FA", "FR", "GA", "GR"}
_FILE_READ_DATA = 0x1
_READ_CONTROL = 0x20000


class WindowsApiError(OSError):
    """A failed system call, with the Windows error code in the message."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    wintypes.LPCWSTR,
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p
_kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

_advapi32.LookupAccountNameW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_advapi32.LookupAccountNameW.restype = wintypes.BOOL
_advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
_advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
_advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.DWORD),
]
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
_advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(wintypes.DWORD),
]
_advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
_advapi32.GetSecurityDescriptorDacl.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.BOOL),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL),
]
_advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
_advapi32.SetNamedSecurityInfoW.argtypes = [
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
_advapi32.GetNamedSecurityInfoW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
_advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _take(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def protect(data: bytes, entropy: bytes) -> bytes:
    """DPAPI with the machine scope: any process on this computer that is allowed to read the
    file can unprotect it (the file's ACL is what limits who that is), and the blob is useless
    on any other computer. `entropy` is an extra secret every call must repeat."""
    source, _keep = _blob(data)
    extra, _keep_extra = _blob(entropy)
    out = _DataBlob()
    flags = _CRYPTPROTECT_UI_FORBIDDEN | _CRYPTPROTECT_LOCAL_MACHINE
    if not _crypt32.CryptProtectData(
        ctypes.byref(source), None, ctypes.byref(extra), None, None, flags, ctypes.byref(out)
    ):
        raise WindowsApiError(f"CryptProtectData failed (error {ctypes.get_last_error()})")
    return _take(out)


def unprotect(blob: bytes, entropy: bytes) -> bytes:
    source, _keep = _blob(blob)
    extra, _keep_extra = _blob(entropy)
    out = _DataBlob()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        ctypes.byref(extra),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    ):
        raise WindowsApiError(f"CryptUnprotectData failed (error {ctypes.get_last_error()})")
    return _take(out)


# --- accounts and SIDs ------------------------------------------------------------------------


def service_sid(service_name: str) -> str:
    """The SID of the virtual account `NT SERVICE\\<name>`.

    It is derived from the service name alone (SHA-1 of the upper-cased UTF-16 name, as five
    little-endian words), so it can be put in an ACL before the service is installed, which is
    when `enroll` runs.
    """
    digest = hashlib.sha1(service_name.upper().encode("utf-16-le"), usedforsecurity=False).digest()
    return "S-1-5-80-" + "-".join(str(part) for part in struct.unpack("<5I", digest))


def resolve_sid(account: str) -> str:
    """The SID of `account` ("DOMAIN\\user", "user" or "NT SERVICE\\name") as `S-1-...`."""
    if account.upper().startswith(_SERVICE_PREFIX):
        return service_sid(account[len(_SERVICE_PREFIX) :])
    sid_size, domain_size, use = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD(0)
    _advapi32.LookupAccountNameW(
        None,
        account,
        None,
        ctypes.byref(sid_size),
        None,
        ctypes.byref(domain_size),
        ctypes.byref(use),
    )
    if sid_size.value == 0:
        raise WindowsApiError(f"Conta não encontrada: {account}")
    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not _advapi32.LookupAccountNameW(
        None,
        account,
        sid,
        ctypes.byref(sid_size),
        domain,
        ctypes.byref(domain_size),
        ctypes.byref(use),
    ):
        raise WindowsApiError(f"Conta não encontrada: {account}")
    text = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise WindowsApiError(f"ConvertSidToStringSid failed (error {ctypes.get_last_error()})")
    try:
        return str(text.value)
    finally:
        _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


# --- ACLs -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ace:
    kind: str  # "A" (allow) or "D" (deny)
    flags: str
    rights: str
    sid: str

    @property
    def can_read(self) -> bool:
        if self.rights.lower().startswith("0x"):
            mask = int(self.rights, 16)
            return bool(mask & _FILE_READ_DATA) and bool(mask & _READ_CONTROL)
        tokens = [self.rights[i : i + 2] for i in range(0, len(self.rights), 2)]
        return any(token in _READ_RIGHTS for token in tokens)


@dataclass(frozen=True)
class Dacl:
    protected: bool  # inheritance from the parent is off
    aces: list[Ace]


def normalize_sid(token: str) -> str:
    """A SID as `S-1-...`, whether SDDL wrote it in full or as a two-letter alias.

    Windows writes the account of the person who set an ACL as an alias when it is a well-known
    one (the local Administrator, RID 500, becomes `LA`), so comparing raw SDDL tokens with SIDs
    from `resolve_sid` would call the agent's own account a stranger.
    """
    if token.startswith("S-1-"):
        return token
    if token in _SDDL_ALIASES:
        return _SDDL_ALIASES[token]
    sid = ctypes.c_void_p()
    if not _advapi32.ConvertStringSidToSidW(token, ctypes.byref(sid)):
        return token  # unknown: left as it is, so it shows up as an unexpected account
    try:
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return token
        try:
            return str(text.value)
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        _kernel32.LocalFree(sid)


_ACE = re.compile(r"\(([^)]*)\)")


def parse_sddl(sddl: str) -> Dacl:
    """The DACL of an SDDL string (`D:PAI(A;OICI;FA;;;SY)(...)`)."""
    start = sddl.index("D:") + 2
    end = sddl.find("S:", start)
    section = sddl[start : end if end != -1 else None]
    flags = section[: section.find("(")] if "(" in section else section
    aces: list[Ace] = []
    for raw in _ACE.findall(section):
        fields = raw.split(";")
        sid = fields[5] if len(fields) > 5 else ""
        aces.append(Ace(fields[0], fields[1], fields[2], normalize_sid(sid)))
    return Dacl(protected="P" in flags, aces=aces)


def _check(code: int, call: str) -> None:
    if code != 0:
        raise WindowsApiError(f"{call} failed (error {code})")


def apply_dacl(path: Path, sddl: str) -> None:
    """Replace the DACL of `path` with the one in `sddl`.

    A `P` in the SDDL (`D:P(...)`) turns inheritance off; without it inheritance is on again.
    """
    descriptor = ctypes.c_void_p()
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise WindowsApiError(f"Invalid SDDL (error {ctypes.get_last_error()}): {sddl}")
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not _advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        ):
            raise WindowsApiError(f"GetSecurityDescriptorDacl failed ({ctypes.get_last_error()})")
        inheritance = (
            _PROTECTED_DACL_SECURITY_INFORMATION
            if parse_sddl(sddl).protected
            else _UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        _check(
            _advapi32.SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | inheritance,
                None,
                None,
                acl,
                None,
            ),
            "SetNamedSecurityInfo",
        )
    finally:
        _kernel32.LocalFree(descriptor)


def _hand_ownership_to_administrators(path: Path) -> None:
    """The owner can always change the ACL (never read the data). Handing ownership to the
    Administrators group needs an elevated process, which is how `enroll` runs for real; in a
    development run without elevation it is skipped and the person keeps ownership."""
    sid = ctypes.c_void_p()
    if not _advapi32.ConvertStringSidToSidW(ADMINISTRATORS_SID, ctypes.byref(sid)):
        return
    try:
        _advapi32.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION, sid, None, None, None
        )
    finally:
        _kernel32.LocalFree(sid)


def restrict_directory(directory: Path, agent_sid: str) -> None:
    """Make `directory` accessible only to the agent's account (read), SYSTEM and Administrators.

    Inheritance is off, so nothing from `ProgramData` (which lets every user read) comes in, and
    whoever ran the command is not granted anything: they get at the key only if they are an
    administrator. Files created inside inherit this when they are created, so a key is never
    readable by anyone else, not even for a moment.
    """
    apply_dacl(
        directory,
        f"D:P(A;OICI;FA;;;{SYSTEM_SID})(A;OICI;FA;;;{ADMINISTRATORS_SID})(A;OICI;FR;;;{agent_sid})",
    )
    _hand_ownership_to_administrators(directory)


def read_dacl(path: Path) -> Dacl:
    """The DACL of a file or folder, as it is on disk (SDDL, any Windows language)."""
    descriptor = ctypes.c_void_p()
    _check(
        _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        ),
        f"GetNamedSecurityInfo({path})",
    )
    try:
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
        ):
            raise WindowsApiError(f"ConvertSecurityDescriptorToString failed ({path})")
        try:
            return parse_sddl(str(text.value))
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        _kernel32.LocalFree(descriptor)


def make_private_file(path: Path, data: bytes) -> None:
    """Create `path` (exclusively) with `data`. The folder's ACL decides who can read it."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def current_session_id() -> int:
    """The Windows session of this process; 0 is where services run, with no desktop."""
    session = wintypes.DWORD()
    if not _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise WindowsApiError(f"ProcessIdToSessionId failed (error {ctypes.get_last_error()})")
    return int(session.value)
