"""M4b spike service body (temporary, removed after the spike). Roles: agent | robot."""

import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, r"C:\spike\src")
from regista_agent import _windows, winpipe, winservice  # noqa: E402

OUT = Path(r"C:\spike\out")
PIPE = "regista-spike-host"
role = sys.argv[1]
agent_sid = _windows.service_sid("SpikeAgent")
robot_sid = _windows.service_sid("SpikeRobot")


def record(name, data):
    (OUT / f"{role}-{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def agent_work(stop):
    res = {}
    try:
        server = winpipe.PipeServer(PIPE, [robot_sid])
        res["pipe_created"] = True
        try:
            winpipe.PipeServer(PIPE, [robot_sid])
            res["second_server"] = "CREATED (bad)"
        except Exception as e:
            res["second_server"] = f"refused: {e}"
        conn = server.accept(timeout=120)
        peer = conn.peer()
        res["peer"] = {"pid": peer.pid, "sid": peer.sid, "image": peer.image, "expected_sid": robot_sid}
        res["peer_sid_ok"] = peer.sid == robot_sid
        conn.send(b'{"type":"ping"}')
        res["reply"] = conn.receive(timeout=30).decode()
        conn.close()
    except Exception:
        res["error"] = traceback.format_exc()
    record("result", res)
    stop.wait(300)


def job_test():
    import psutil

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]

    class Basic(ctypes.Structure):
        _fields_ = [
            ("a", ctypes.c_int64),
            ("b", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("c", ctypes.c_size_t),
            ("d", ctypes.c_size_t),
            ("e", wintypes.DWORD),
            ("f", ctypes.c_size_t),
            ("g", wintypes.DWORD),
            ("h", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in "abcdef"]

    class Ext(ctypes.Structure):
        _fields_ = [
            ("Basic", Basic),
            ("IO", IO),
            ("p", ctypes.c_size_t),
            ("j", ctypes.c_size_t),
            ("pm", ctypes.c_size_t),
            ("jm", ctypes.c_size_t),
        ]

    job = k.CreateJobObjectW(None, None)
    info = Ext()
    info.Basic.LimitFlags = 0x2000
    assert k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)), ctypes.get_last_error()
    code = (
        "import subprocess,sys,time;"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);"
        "open(sys.argv[1],'w').write(str(c.pid));time.sleep(300)"
    )
    marker = OUT / "grandchild.pid"
    p = subprocess.Popen([sys.executable, "-c", code, str(marker)])
    assert k.AssignProcessToJobObject(job, int(p._handle)), ctypes.get_last_error()  # type: ignore[attr-defined]
    for _ in range(50):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    gpid = int(marker.read_text())
    alive_before = psutil.pid_exists(gpid) and psutil.pid_exists(p.pid)
    k.TerminateJobObject(job, 1)
    time.sleep(1.5)
    return {
        "alive_before": alive_before,
        "child_alive_after": p.poll() is None,
        "grandchild_alive_after": psutil.pid_exists(gpid),
    }


def chromium_test():
    tmp = Path(r"C:\spike\robot-tmp")
    tmp.mkdir(exist_ok=True)
    env = {
        "SYSTEMROOT": os.environ["SYSTEMROOT"],
        "PATH": os.environ["SYSTEMROOT"] + r"\System32",
        "PLAYWRIGHT_BROWSERS_PATH": r"C:\spike\browsers",
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "LOCALAPPDATA": str(tmp),
        "APPDATA": str(tmp),
        "USERPROFILE": str(tmp),
        "HOME": str(tmp),
    }
    script = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        " b=p.chromium.launch(); pg=b.new_page(); pg.goto('data:text/html,<title>ok</title>');"
        " print('TITLE', pg.title()); b.close()\n"
    )
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
    return {
        "rc": r.returncode,
        "stdout": r.stdout[-500:],
        "stderr": r.stderr[-1500:],
        "seconds": round(time.monotonic() - t, 1),
    }


def robot_work(stop):
    res = {"whoami": subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()}
    try:
        conn = winpipe.connect(PIPE, timeout=60)
        peer = conn.peer()
        res["server"] = {"pid": peer.pid, "sid": peer.sid, "image": peer.image, "expected_sid": agent_sid}
        res["server_sid_ok"] = peer.sid == agent_sid
        res["scm_pid"] = winpipe.service_process_id("SpikeAgent")
        res["server_is_service"] = winpipe.server_is_service(conn, "SpikeAgent")
        res["server_is_other_service"] = winpipe.server_is_service(conn, "SpikeRobot")
        res["got"] = conn.receive(timeout=30).decode()
        conn.send(b'{"type":"hello"}')
        conn.close()
    except Exception:
        res["pipe_error"] = traceback.format_exc()
    for name, fn in (("job", job_test), ("chromium", chromium_test)):
        try:
            res[name] = fn()
        except Exception:
            res[name] = {"error": traceback.format_exc()}
    record("result", res)
    stop.wait(300)


rc = winservice.run_service("Spike" + role.capitalize(), agent_work if role == "agent" else robot_work)
if rc:
    (OUT / f"{role}-dispatch-error.txt").write_text(str(rc))
