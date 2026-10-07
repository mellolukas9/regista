"""A named pipe between the agent and the robot host, with the identity checks (Windows only).

The agent is the server: one pipe, **one instance** (`FILE_FLAG_FIRST_PIPE_INSTANCE`, a maximum of
one), a DACL that lets in only the host's SID (and SYSTEM), and no remote clients. The host is the
client and checks who the server is. Nothing here trusts a name: who is on the other end is read
from the kernel (the token the pipe authenticated and the process id), never from what the other
side says.

All calls are synchronous with a timeout (overlapped I/O under the hood), so a silent peer can
never hang the agent: the caller gets `PipeTimeout`.

Everything is under one `if`: the Windows calls do not exist elsewhere, and the type checker (which
also runs on Linux) must not look at them there.
"""

import sys

if sys.platform == "win32":
    import ctypes
    import struct
    from ctypes import wintypes
    from dataclasses import dataclass

    from regista_agent import _windows

    PIPE_PREFIX = "\\\\.\\pipe\\"
    MAX_FRAME_BYTES = 1_048_576  # one message; the protocol keeps its own, smaller, limits too

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    _PIPE_ACCESS_DUPLEX = 0x00000003
    _PIPE_TYPE_BYTE = 0x0
    _PIPE_READMODE_BYTE = 0x0
    _PIPE_WAIT = 0x0
    _PIPE_REJECT_REMOTE_CLIENTS = 0x8
    _SECURITY_SQOS_PRESENT = 0x00100000
    _SECURITY_IDENTIFICATION = 0x00010000
    _ERROR_IO_PENDING = 997
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_BROKEN_PIPE = 109
    _ERROR_PIPE_BUSY = 231
    _ERROR_ACCESS_DENIED = 5
    _ERROR_FILE_NOT_FOUND = 2
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SECURITY_DESCRIPTOR_REVISION = 1
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class PipeError(OSError):
        """The pipe is unusable (broken, refused, wrong peer)."""

    class PipeTimeout(PipeError):
        """The peer did not answer in time."""

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _a32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _k32.CreateNamedPipeW.restype = wintypes.HANDLE
    _k32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes),
    ]  # fmt: skip
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, wintypes.HANDLE,
    ]  # fmt: skip
    _k32.ConnectNamedPipe.restype = wintypes.BOOL
    _k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _k32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_Overlapped),
    ]  # fmt: skip
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = _k32.ReadFile.argtypes
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    ]  # fmt: skip
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    _k32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]  # fmt: skip
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _a32.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
    _a32.RevertToSelf.argtypes = []
    _a32.OpenThreadToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE),
    ]  # fmt: skip
    _a32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]  # fmt: skip
    _a32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]  # fmt: skip
    _a32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _a32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]  # fmt: skip
    _k32.GetCurrentThread.restype = wintypes.HANDLE

    def _error(what: str) -> PipeError:
        code = ctypes.get_last_error()
        if code == _ERROR_ACCESS_DENIED:
            return PipeError(f"{what}: acesso negado ao pipe (erro 5)")
        return PipeError(f"{what} falhou (erro {code})")

    def _sid_string(sid_pointer: int) -> str:
        text = wintypes.LPWSTR()
        if not _a32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise _error("ConvertSidToStringSid")
        try:
            return str(text.value)
        finally:
            _k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def _token_user_sid(token: int) -> str:
        needed = wintypes.DWORD()
        _a32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not _a32.GetTokenInformation(token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)):
            raise _error("GetTokenInformation")
        entry = ctypes.cast(buffer, ctypes.POINTER(_SidAndAttributes)).contents
        return _sid_string(entry.Sid)

    def process_image(pid: int) -> str | None:
        """The path of the executable of a process, or None if this account may not look."""
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not _k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            _k32.CloseHandle(handle)

    def process_user_sid(pid: int) -> str | None:
        """The user SID of a process's token, or None if this account may not look."""
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            token = wintypes.HANDLE()
            if not _a32.OpenProcessToken(handle, _TOKEN_QUERY, ctypes.byref(token)):
                return None
            try:
                return _token_user_sid(token.value or 0)
            finally:
                _k32.CloseHandle(token)
        finally:
            _k32.CloseHandle(handle)

    @dataclass(frozen=True)
    class PeerIdentity:
        """Who is on the other end of the pipe, as the kernel says."""

        pid: int
        sid: str | None  # from the token the pipe authenticated (server side only)
        image: str | None  # None when this account is not allowed to look at the process

    class PipeConnection:
        """One end of a connected pipe: framed messages (4-byte length, then the bytes)."""

        def __init__(self, handle: int, *, server: bool) -> None:
            self._handle = handle
            self._server = server
            self._closed = False

        # --- raw I/O with a timeout ------------------------------------------------------------

        def _wait(self, overlapped: _Overlapped, event: int, timeout: float, what: str) -> int:
            result = _k32.WaitForSingleObject(event, int(timeout * 1000))
            if result == _WAIT_TIMEOUT:
                _k32.CancelIoEx(self._handle, ctypes.byref(overlapped))
                _k32.WaitForSingleObject(event, 1000)
                raise PipeTimeout(f"{what}: o outro lado não respondeu em {timeout:g} s")
            if result != _WAIT_OBJECT_0:
                raise _error(what)
            done = wintypes.DWORD()
            if not _k32.GetOverlappedResult(
                self._handle, ctypes.byref(overlapped), ctypes.byref(done), False
            ):
                code = ctypes.get_last_error()
                if code == _ERROR_BROKEN_PIPE:
                    raise PipeError("o pipe foi fechado pelo outro lado")
                raise PipeError(f"{what} falhou (erro {code})")
            return int(done.value)

        def _io(self, call: object, data: object, size: int, timeout: float, what: str) -> int:
            event = _k32.CreateEventW(None, True, False, None)
            overlapped = _Overlapped()
            overlapped.hEvent = event
            transferred = wintypes.DWORD()
            try:
                ok = call(
                    self._handle, data, size, ctypes.byref(transferred), ctypes.byref(overlapped)
                )  # type: ignore[operator]
                if not ok:
                    code = ctypes.get_last_error()
                    if code == _ERROR_BROKEN_PIPE:
                        raise PipeError("o pipe foi fechado pelo outro lado")
                    if code != _ERROR_IO_PENDING:
                        raise PipeError(f"{what} falhou (erro {code})")
                    return self._wait(overlapped, event, timeout, what)
                return int(transferred.value)
            finally:
                _k32.CloseHandle(event)

        def _read_exact(self, count: int, timeout: float) -> bytes:
            out = bytearray()
            while len(out) < count:
                chunk = ctypes.create_string_buffer(count - len(out))
                got = self._io(_k32.ReadFile, chunk, count - len(out), timeout, "leitura do pipe")
                if got == 0:
                    raise PipeError("o pipe foi fechado pelo outro lado")
                out += chunk.raw[:got]
            return bytes(out)

        def _write_all(self, data: bytes, timeout: float) -> None:
            view = memoryview(data)
            sent = 0
            while sent < len(data):
                piece = ctypes.create_string_buffer(bytes(view[sent:]), len(data) - sent)
                sent += self._io(
                    _k32.WriteFile, piece, len(data) - sent, timeout, "escrita no pipe"
                )

        # --- messages --------------------------------------------------------------------------

        def send(self, payload: bytes, *, timeout: float = 10.0) -> None:
            if len(payload) > MAX_FRAME_BYTES:
                raise PipeError("mensagem grande demais para o pipe")
            self._write_all(struct.pack("<I", len(payload)) + payload, timeout)

        def receive(self, *, timeout: float = 10.0) -> bytes:
            """The next whole message. A length above the limit is a protocol violation: the pipe
            is unusable from then on (the caller closes it), and nothing is allocated for it."""
            (length,) = struct.unpack("<I", self._read_exact(4, timeout))
            if length > MAX_FRAME_BYTES:
                raise PipeError(f"mensagem de {length} bytes passa do limite do pipe")
            return self._read_exact(length, timeout) if length else b""

        # --- who is there ----------------------------------------------------------------------

        def peer(self) -> PeerIdentity:
            """The other end. On the server side the SID comes from the token the pipe
            authenticated (the client connected at the identification level, so this reads who it
            is and nothing more); the process id and image come from the kernel too."""
            pid = wintypes.ULONG()
            getter = (
                _k32.GetNamedPipeClientProcessId
                if self._server
                else _k32.GetNamedPipeServerProcessId
            )
            if not getter(self._handle, ctypes.byref(pid)):
                raise _error("pipe peer pid")
            sid = self._identify_client() if self._server else process_user_sid(pid.value)
            return PeerIdentity(pid=int(pid.value), sid=sid, image=process_image(pid.value))

        def _identify_client(self) -> str:
            if not _a32.ImpersonateNamedPipeClient(self._handle):
                raise _error("ImpersonateNamedPipeClient")
            try:
                token = wintypes.HANDLE()
                if not _a32.OpenThreadToken(
                    _k32.GetCurrentThread(), _TOKEN_QUERY, True, ctypes.byref(token)
                ):
                    raise _error("OpenThreadToken")
                try:
                    return _token_user_sid(token.value or 0)
                finally:
                    _k32.CloseHandle(token)
            finally:
                _a32.RevertToSelf()

        def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            if self._server:
                _k32.DisconnectNamedPipe(self._handle)
            _k32.CloseHandle(self._handle)

    class PipeServer:
        """The agent's end: creates the pipe (one instance, only these SIDs may connect)."""

        def __init__(self, name: str, allowed_sids: list[str]) -> None:
            self.path = PIPE_PREFIX + name
            if not allowed_sids:
                raise ValueError("uma lista vazia de SIDs deixaria o pipe sem ninguém")
            for sid in allowed_sids:
                if not sid.startswith("S-1-"):
                    raise ValueError(f"não é um SID: {sid}")
            aces = "".join(f"(A;;GRGW;;;{sid})" for sid in allowed_sids)
            # Protected DACL: SYSTEM and the allowed accounts, nobody else (not even Everyone).
            sddl = f"D:P(A;;GA;;;SY){aces}"
            descriptor = ctypes.c_void_p()
            if not _a32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, _SECURITY_DESCRIPTOR_REVISION, ctypes.byref(descriptor), None
            ):
                raise _error("SDDL do pipe")
            attributes = _SecurityAttributes(
                ctypes.sizeof(_SecurityAttributes), descriptor.value, False
            )
            try:
                handle = _k32.CreateNamedPipeW(
                    self.path,
                    _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED | _FILE_FLAG_FIRST_PIPE_INSTANCE,
                    _PIPE_TYPE_BYTE
                    | _PIPE_READMODE_BYTE
                    | _PIPE_WAIT
                    | _PIPE_REJECT_REMOTE_CLIENTS,
                    1,  # a single instance: whoever holds the connection is the only one
                    65536,
                    65536,
                    0,
                    ctypes.byref(attributes),
                )
            finally:
                _k32.LocalFree(descriptor)
            if handle == _INVALID_HANDLE_VALUE or not handle:
                raise _error("criar o pipe (já existe outro servidor com esse nome?)")
            self._handle = handle

        def accept(self, *, timeout: float = 30.0) -> PipeConnection:
            event = _k32.CreateEventW(None, True, False, None)
            overlapped = _Overlapped()
            overlapped.hEvent = event
            try:
                ok = _k32.ConnectNamedPipe(self._handle, ctypes.byref(overlapped))
                code = ctypes.get_last_error()
                if not ok and code == _ERROR_PIPE_CONNECTED:
                    pass  # the client was already there
                elif not ok and code == _ERROR_IO_PENDING:
                    result = _k32.WaitForSingleObject(event, int(timeout * 1000))
                    if result == _WAIT_TIMEOUT:
                        _k32.CancelIoEx(self._handle, ctypes.byref(overlapped))
                        raise PipeTimeout("ninguém conectou ao pipe")
                    if result != _WAIT_OBJECT_0:
                        raise _error("esperar conexão")
                elif not ok:
                    raise PipeError(f"ConnectNamedPipe falhou (erro {code})")
            finally:
                _k32.CloseHandle(event)
            return PipeConnection(self._handle, server=True)

        def close(self) -> None:
            _k32.CloseHandle(self._handle)

    def connect(name: str, *, timeout: float = 10.0) -> PipeConnection:
        """The host's end. Connects at the **identification** level: the server may learn who we
        are, but a server that is not who it should be cannot act with our rights."""
        import time

        deadline = time.monotonic() + timeout
        while True:
            handle = _k32.CreateFileW(
                PIPE_PREFIX + name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
                None,
            )
            if handle != _INVALID_HANDLE_VALUE and handle:
                return PipeConnection(handle, server=False)
            code = ctypes.get_last_error()
            if code not in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY):
                raise PipeError(f"conectar ao pipe falhou (erro {code})")
            if time.monotonic() >= deadline:
                raise PipeTimeout(
                    "o pipe do agente não está disponível"
                    if code == _ERROR_FILE_NOT_FOUND
                    else "o pipe do agente está ocupado por outro cliente"
                )
            time.sleep(0.2)

    def sid_of_service(service_name: str) -> str:
        """The SID of a service's virtual account (`NT SERVICE\\<name>`)."""
        return _windows.service_sid(service_name)
