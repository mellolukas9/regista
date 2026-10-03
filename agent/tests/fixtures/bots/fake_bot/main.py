"""A robot for the agent tests. What it does is chosen by the job parameters, as a real one would.

mode: ok | fail | wait | stubborn | env | secrets | noisy
"""

import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
params = json.loads(os.environ.get("REGISTA_JOB_PARAMS", "{}"))
mode = params.get("mode", "ok")
artifacts = Path(os.environ["REGISTA_ARTIFACTS_DIR"])
cancel_file = Path(os.environ["REGISTA_CANCEL_FILE"])


def shot(name: str = "tela.png") -> None:
    (artifacts / name).write_bytes(PNG)


print("INFO robo iniciado", flush=True)
print(f"INFO pid {os.getpid()}", flush=True)

if mode == "ok":
    print("passo um", flush=True)
    print("WARNING passo dois", flush=True)
    shot()
    print("INFO fim", flush=True)
elif mode == "fail":
    shot("erro.png")
    print("ERROR algo deu errado", flush=True)
    print("Traceback (most recent call last):", file=sys.stderr, flush=True)
    print('  File "main.py", line 1, in <module>', file=sys.stderr, flush=True)
    print("ValueError: boom", file=sys.stderr, flush=True)
    sys.exit(2)
elif mode == "wait":
    # Looks at the cancel file between "items", like a robot that cooperates.
    while not cancel_file.exists():
        time.sleep(0.1)
    print("INFO cancelamento visto, saindo", flush=True)
    sys.exit(0)
elif mode == "stubborn":
    # Ignores polite requests and leaves a child behind: only killing the tree stops it.
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(f"INFO child {child.pid}", flush=True)
    print(f"INFO self {os.getpid()}", flush=True)
    while True:
        time.sleep(0.2)
elif mode == "env":
    interesting = {k: v for k, v in os.environ.items() if k.startswith("REGISTA_")}
    print("INFO ENV " + json.dumps(interesting), flush=True)
    print("INFO NAMES " + json.dumps(sorted(os.environ)), flush=True)
    print("INFO CWD " + os.getcwd(), flush=True)
elif mode == "secrets":
    print("token=hunter2 and rga1.AAAA.BBBB and Bearer abc123 and rgk_zzzz", flush=True)
    print("\x1b[31mvermelho\x1b[0m", flush=True)
elif mode == "noisy":
    print("x" * 9000, flush=True)
    for n in range(int(params.get("lines", 0))):
        print(f"linha {n}", flush=True)
