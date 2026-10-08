"""A minimal Windows service wrapper in ctypes (Windows only).

Just enough of the Service Control Manager protocol for a Python process to be a service: report
RUNNING, run the work, stop when asked. No WinSW, no pywin32 (M8 replaces this with the packaged
installer, ADR 0022). Everything is under one `if`: the calls do not exist on other systems.
"""

import sys

if sys.platform == "win32":
    import ctypes
    import threading
    from collections.abc import Callable
    from ctypes import wintypes

    _SERVICE_WIN32_OWN_PROCESS = 0x10
    _SERVICE_START_PENDING = 2
    _SERVICE_STOP_PENDING = 3
    _SERVICE_STOPPED = 1
    _SERVICE_RUNNING = 4
    _SERVICE_ACCEPT_STOP = 0x1
    _SERVICE_ACCEPT_SHUTDOWN = 0x4
    _SERVICE_CONTROL_STOP = 1
    _SERVICE_CONTROL_SHUTDOWN = 5
    _NO_ERROR = 0

    class _Status(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        ]

    _MainFn = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
    _HandlerFn = ctypes.WINFUNCTYPE(
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p
    )

    class _TableEntry(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", _MainFn)]

    _a32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _a32.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
    _a32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, _HandlerFn, ctypes.c_void_p]
    _a32.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Status)]
    _a32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(_TableEntry)]

    def run_service(name: str, work: Callable[[threading.Event], None]) -> int:
        """Hand this process to the service manager. `work(stop)` runs until it returns or `stop`
        is set (the manager asked to stop). Returns 0, or the Windows error when this process was
        not started by the service manager (for instance, from a console)."""
        stop = threading.Event()
        state: dict[str, int | None] = {"handle": None}
        status = _Status()

        def report(current: int, exit_code: int = _NO_ERROR, hint: int = 0) -> None:
            status.dwServiceType = _SERVICE_WIN32_OWN_PROCESS
            status.dwCurrentState = current
            status.dwControlsAccepted = (
                0
                if current in (_SERVICE_START_PENDING, _SERVICE_STOP_PENDING)
                else _SERVICE_ACCEPT_STOP | _SERVICE_ACCEPT_SHUTDOWN
            )
            status.dwWin32ExitCode = exit_code
            status.dwWaitHint = hint
            _a32.SetServiceStatus(state["handle"], ctypes.byref(status))

        def handler(control: int, _event: int, _data: object, _context: object) -> int:
            if control in (_SERVICE_CONTROL_STOP, _SERVICE_CONTROL_SHUTDOWN):
                report(_SERVICE_STOP_PENDING, hint=15000)
                stop.set()
            return _NO_ERROR

        handler_fn = _HandlerFn(handler)

        def service_main(_argc: int, _argv: object) -> None:
            state["handle"] = _a32.RegisterServiceCtrlHandlerExW(name, handler_fn, None)
            report(_SERVICE_START_PENDING, hint=10000)
            report(_SERVICE_RUNNING)
            code = _NO_ERROR
            try:
                work(stop)
            except Exception:
                code = 1
            report(_SERVICE_STOPPED, exit_code=code)

        main_fn = _MainFn(service_main)
        table = (_TableEntry * 2)(_TableEntry(name, main_fn), _TableEntry(None, _MainFn()))
        if not _a32.StartServiceCtrlDispatcherW(table):
            return ctypes.get_last_error()
        return 0
