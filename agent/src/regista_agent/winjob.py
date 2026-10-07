"""A Windows Job Object that holds one robot and everything it starts (Windows only; ADR 0022).

`KILL_ON_JOB_CLOSE`: when the host (the only holder of the job's handle) goes away for any reason,
Windows kills every process in the job, so a robot never outlives its host. `terminate` kills the
whole tree at once, including a browser the robot started and any process that tried to escape the
parent/child chain (what walking the process tree cannot guarantee).

The process is created **suspended**, put in the job, and only then resumed, so it cannot start a
child before it is contained.
"""

import sys

if sys.platform == "win32":
    import ctypes
    import subprocess
    from ctypes import wintypes
    from typing import Any

    CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in "abcdef"]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _nt = ctypes.WinDLL("ntdll")
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]

    class Job:
        def __init__(self) -> None:
            handle = _k32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(f"CreateJobObject falhou (erro {ctypes.get_last_error()})")
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                code = ctypes.get_last_error()
                _k32.CloseHandle(handle)
                raise OSError(f"SetInformationJobObject falhou (erro {code})")
            self._handle: int | None = handle

        def start(self, args: list[str], **kwargs: Any) -> "subprocess.Popen[bytes]":
            """Start `args` suspended, contain it, then let it run."""
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_SUSPENDED
            popen = subprocess.Popen(args, **kwargs)  # noqa: S603  (a fixed list, no shell)
            process = int(popen._handle)  # type: ignore[attr-defined]
            try:
                if self._handle is None or not _k32.AssignProcessToJobObject(self._handle, process):
                    raise OSError(
                        f"AssignProcessToJobObject falhou (erro {ctypes.get_last_error()})"
                    )
                _nt.NtResumeProcess(process)
            except BaseException:
                popen.kill()
                raise
            return popen

        def terminate(self) -> None:
            """Kill everything in the job, now."""
            if self._handle is not None:
                _k32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            """Closing the last handle kills what is still in the job (KILL_ON_JOB_CLOSE)."""
            if self._handle is not None:
                _k32.CloseHandle(self._handle)
                self._handle = None
