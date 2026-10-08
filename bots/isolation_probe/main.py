"""Robô de prova do isolamento entre o agente e o robô (ADR 0022). Inofensivo e versionado.

Roda como qualquer robô e **tenta**, uma a uma, coisas que um robô não pode fazer (ler a chave da
máquina, alterar a configuração, o kill switch, os pacotes, o cache, o runtime, a própria versão) e
coisas que pode (escrever em `tmp` e `artifacts`, rodar o próprio código). Cada tentativa vira uma
linha de log:

    INFO PROBE ler a chave da máquina: NEGADA (PermissionError)
    ERROR PROBE escrever em packages: PERMITIDA        <- isolamento quebrado

Termina com código 0 só se **toda** tentativa proibida foi negada e toda permitida funcionou.
Serve à CI do Windows (com as contas e os serviços de verdade) e à conferência manual.

Parâmetros (JSON em REGISTA_JOB_PARAMS):
- `home`: a pasta do agente (padrão `%ProgramData%\\Regista`);
- `phase`: `probe` (padrão), `wait` (fica até o cancelamento, com um processo filho), `persist`
  (grava no registro do usuário, como um robô malicioso faria para afetar a próxima execução) ou
  `verify` (confere que a execução seguinte não foi afetada e limpa o que `persist` gravou);
- `browser` (padrão verdadeiro; o painel dispara sem parâmetros): abre uma página local no
  Chromium do Playwright;
- `junction` (padrão verdadeiro): deixa em `artifacts` uma junction para a pasta da chave (o
  agente não pode seguir esse link ao enviar capturas).
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
params = json.loads(os.environ.get("REGISTA_JOB_PARAMS", "{}"))
HOME = Path(
    params.get("home") or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Regista"
)
PHASE = params.get("phase", "probe")
ARTIFACTS = Path(os.environ["REGISTA_ARTIFACTS_DIR"])
TMP = Path(os.environ["TEMP"])
CANCEL_FILE = Path(os.environ["REGISTA_CANCEL_FILE"])
BOT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = BOT_DIR.parent
RUN_DIR = PACKAGE_DIR.parent
PIPE = r"\\.\pipe\regista-robot-host"

forbidden_allowed = 0  # a forbidden thing that worked: isolation is broken
allowed_denied = 0  # something the robot needs that was refused


def say(level: str, text: str) -> None:
    print(f"{level} {text}", flush=True)


def must_be_denied(what: str, action) -> None:
    global forbidden_allowed
    try:
        action()
    except (PermissionError, FileNotFoundError, OSError) as exc:
        say("INFO", f"PROBE {what}: NEGADA ({type(exc).__name__})")
    else:
        forbidden_allowed += 1
        say("ERROR", f"PROBE {what}: PERMITIDA")


def must_work(what: str, action) -> None:
    global allowed_denied
    try:
        action()
    except Exception as exc:
        allowed_denied += 1
        say("ERROR", f"PROBE {what}: FALHOU ({type(exc).__name__}: {exc})")
    else:
        say("INFO", f"PROBE {what}: OK")


def write(path: Path, data: bytes = b"x") -> None:
    with open(path, "ab") as handle:
        handle.write(data)


def read(path: Path) -> None:
    with open(path, "rb") as handle:
        handle.read(1)


def listing(path: Path) -> None:
    next(iter(os.scandir(path)), None)


def connect_pipe() -> None:
    with open(PIPE, "r+b", buffering=0):
        pass


def probe() -> None:
    # --- what a robot must not do ---------------------------------------------------------
    must_be_denied("ler a chave da máquina", lambda: read(HOME / "keys" / "machine.key"))
    must_be_denied("ler a identidade da máquina", lambda: read(HOME / "keys" / "identity.json"))
    must_be_denied("listar a pasta da chave", lambda: listing(HOME / "keys"))
    must_be_denied("ler agent.toml", lambda: read(HOME / "agent.toml"))
    must_be_denied("alterar agent.toml", lambda: write(HOME / "agent.toml", b"\n"))
    must_be_denied("ligar o kill switch (criar PAUSED)", lambda: write(HOME / "PAUSED"))
    must_be_denied("escrever na raiz do agente", lambda: write(HOME / "probe.txt"))
    must_be_denied("listar a raiz do agente", lambda: listing(HOME))
    for folder in ("packages", "uv-cache", "logs", "keys", "python", "browsers"):
        must_be_denied(f"escrever em {folder}", lambda f=folder: write(HOME / f / "probe.txt"))
    must_be_denied("listar packages", lambda: listing(HOME / "packages"))
    must_be_denied("listar uv-cache", lambda: listing(HOME / "uv-cache"))
    must_be_denied("escrever em runs", lambda: write(HOME / "runs" / "probe.txt"))
    must_be_denied("listar runs", lambda: listing(HOME / "runs"))
    must_be_denied(
        "escrever na pasta de outra execução", lambda: write(HOME / "runs" / "ffffffffffff" / "x")
    )
    must_be_denied("escrever na raiz da própria execução", lambda: write(RUN_DIR / "probe.txt"))
    must_be_denied("alterar o arquivo de cancelamento", lambda: write(CANCEL_FILE, b"x"))
    must_be_denied("ler o que montou o ambiente (build)", lambda: listing(RUN_DIR / "build"))
    must_be_denied("alterar o código da própria versão", lambda: write(BOT_DIR / "main.py"))
    must_be_denied("criar arquivo no pacote", lambda: write(PACKAGE_DIR / "probe.txt"))
    must_be_denied(
        "plantar código no ambiente",
        lambda: write(Path(sys.prefix) / "Lib" / "site-packages" / "probe.pth"),
    )
    must_be_denied("alterar o Python do ambiente", lambda: write(Path(sys.executable)))
    must_be_denied("falar no canal do hospedeiro como cliente", connect_pipe)

    # --- what a robot needs --------------------------------------------------------------
    must_work("escrever em tmp", lambda: write(TMP / "probe.txt"))
    must_work("criar pasta em tmp", lambda: (TMP / "sub").mkdir(exist_ok=True))
    must_work(
        "escrever uma captura em artifacts", lambda: (ARTIFACTS / "tela.png").write_bytes(PNG)
    )
    must_work("ler o próprio código", lambda: read(BOT_DIR / "main.py"))
    must_work("rodar com o Python do ambiente da execução", lambda: _in_run_venv())
    must_work("o ambiente não traz segredos do agente", lambda: _clean_environment())

    if params.get("junction", True):
        link = ARTIFACTS / "evil.png"
        must_work(
            "deixar uma junction em artifacts apontando para a pasta da chave",
            lambda: subprocess.run(  # noqa: S603
                ["cmd", "/c", "mklink", "/J", str(link), str(HOME / "keys")],  # noqa: S607
                check=True,
                capture_output=True,
            ),
        )
    if params.get("browser", True):
        must_work("abrir uma página no Chromium", _browser)


def _in_run_venv() -> None:
    if RUN_DIR not in Path(sys.executable).resolve().parents:
        raise RuntimeError(f"Python fora da pasta da execução: {sys.executable}")


def _clean_environment() -> None:
    leaked = [
        name
        for name in os.environ
        if name.upper().startswith("REGISTA_")
        and name
        not in (
            "REGISTA_JOB_ID",
            "REGISTA_JOB_PARAMS",
            "REGISTA_ARTIFACTS_DIR",
            "REGISTA_CANCEL_FILE",
        )
    ]
    if leaked:
        raise RuntimeError(f"variáveis do agente no ambiente do robô: {leaked}")
    if (
        os.environ.get("USERPROFILE", "").lower().startswith(("c:\\users\\", "c:\\windows"))
        and params.get("expect_profile") == "run"
    ):
        raise RuntimeError("o perfil do robô não está dentro da pasta da execução")


def _browser() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("data:text/html,<title>ok</title><h1>isolation_probe</h1>")
        say("INFO", f"PROBE título da página: {page.title()}")
        browser.close()


def wait() -> None:
    """Stays until the run is cancelled, with a child process beside it (like a browser): lets the
    CI check that cancelling or losing the host takes the whole tree down."""
    import time

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    say("INFO", f"PROBE filho {child.pid}")
    say("INFO", f"PROBE esperando o cancelamento (pid {os.getpid()})")
    while not CANCEL_FILE.exists():
        time.sleep(0.2)
    say("INFO", "PROBE cancelamento visto")


def persist() -> None:
    """What a malicious robot would try, so that the NEXT run runs its code."""
    import winreg

    evil = TMP / "evil-path"
    evil.mkdir(exist_ok=True)
    (evil / "sitecustomize.py").write_text("import os; os.environ['REGISTA_PROBE_RAN'] = '1'\n")
    try:
        for version in ("3.13", "3.12"):
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                rf"Software\Python\PythonCore\{version}\PythonPath",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(evil))
        env = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(env, "REGISTA_PROBE_PERSISTED", 0, winreg.REG_SZ, "1")
    except OSError as exc:  # an account with no registry hive of its own: nothing to plant
        say("INFO", f"PROBE o registro do usuário não aceitou a gravação: NEGADA ({exc})")
    else:
        say("INFO", "PROBE gravei PythonPath e uma variável no registro do usuário (HKCU)")


def verify() -> None:
    """The run after `persist`: its Python and its environment must be the same as always."""
    import winreg

    global forbidden_allowed
    if any("evil-path" in entry for entry in sys.path):
        forbidden_allowed += 1
        say("ERROR", "PROBE o PythonPath do registro afetou o sys.path desta execução")
    else:
        say("INFO", "PROBE sys.path não foi afetado pelo registro (HKCU PythonPath): NEGADA")
    if "REGISTA_PROBE_PERSISTED" in os.environ or "REGISTA_PROBE_RAN" in os.environ:
        forbidden_allowed += 1
        say("ERROR", "PROBE o registro do usuário afetou o ambiente desta execução")
    else:
        say("INFO", "PROBE o ambiente não foi afetado pelo registro (HKCU Environment): NEGADA")
    for version in ("3.13", "3.12"):
        for sub in (
            rf"Software\Python\PythonCore\{version}\PythonPath",
            rf"Software\Python\PythonCore\{version}",
        ):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "REGISTA_PROBE_PERSISTED")
    except OSError:
        pass


def whoami() -> str:
    try:
        return subprocess.run(
            ["whoami"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
    except OSError:
        return "?"


say("INFO", f"isolation_probe: fase {PHASE}, executando como {whoami()}")
if PHASE == "wait":
    wait()
elif PHASE == "persist":
    persist()
elif PHASE == "verify":
    verify()
else:
    probe()
say(
    "INFO",
    f"RESULTADO proibidas_permitidas={forbidden_allowed} necessarias_negadas={allowed_denied}",
)
sys.exit(0 if forbidden_allowed == 0 and allowed_denied == 0 else 1)
