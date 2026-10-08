"""The robot host, with the real services, the real accounts and the real pipe (docs/adr/0022).

Everything here runs as it would on a customer's machine: the program is installed in a folder only
administrators can change, `regista-agent service install` creates the two services
(`NT SERVICE\\RegistaAgent` and `NT SERVICE\\RegistaRobot`), a fake HTTP server plays the Regista
server, and a signed package of `bots/isolation_probe` runs through the host. The probe **tries**
to read the key, change the settings, plant code, follow a junction and talk to the pipe, and
reports each attempt; the tests read those lines and check what the agent did.

It creates services and local users, so it only runs when `REGISTA_TEST_REAL_SERVICES=1` in an
elevated session (the `agent-windows` job of the CI). Nowhere else.

Everything is under one `if`: the Windows calls do not exist on other systems, and the type
checker (which also runs on Linux) must not look at them there.
"""

import sys

if sys.platform == "win32":
    import ctypes
    import json
    import os
    import secrets
    import shutil
    import subprocess
    import time
    import uuid
    from collections.abc import Iterator
    from contextlib import contextmanager
    from ctypes import wintypes
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any

    import psutil
    import pytest
    import uv

    from regista_agent import _windows, layout, winpipe
    from regista_agent.config import AgentSettings
    from regista_pack import build as pack_build

    from .fake_server import FakeRegistaServer
    from .package_support import new_key

    pytestmark = pytest.mark.skipif(
        os.environ.get("REGISTA_TEST_REAL_SERVICES") != "1"
        or not ctypes.windll.shell32.IsUserAnAdmin(),
        reason="creates services and users: needs REGISTA_TEST_REAL_SERVICES=1 and an elevation",
    )

    REPO = Path(__file__).resolve().parents[2]
    APP = Path(r"C:\RegistaApp")
    HOME = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Regista"
    PROBE = REPO / "bots" / "isolation_probe"
    SERVICES = ("RegistaRobot", "RegistaAgent")

    def run(
        args: list[str], *, check: bool = True, env: dict[str, str] | None = None
    ) -> "subprocess.CompletedProcess[str]":
        result = subprocess.run(  # noqa: S603  (fixed programs, generated arguments)
            args, capture_output=True, text=True, check=False, errors="replace", env=env
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"{' '.join(args)} -> {result.returncode}\n{result.stdout}\n{result.stderr}"
            )
        return result

    def sc(*args: str, check: bool = True) -> "subprocess.CompletedProcess[str]":
        return run(["sc.exe", *args], check=check)

    def service_state(name: str) -> str:
        out = sc("query", name, check=False).stdout
        for line in out.splitlines():
            if "STATE" in line:
                return line.split(":", 1)[1].split()[1]
        return "MISSING"

    def service_pid(name: str) -> int:
        for line in sc("queryex", name).stdout.splitlines():
            if "PID" in line:
                return int(line.split(":", 1)[1].strip())
        raise AssertionError(f"sem PID para {name}")

    def wait_until(condition: Any, seconds: float = 60.0, what: str = "a condição") -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.5)
        raise AssertionError(f"{what} não aconteceu em {seconds:g} s")

    @dataclass
    class Machine:
        python: Path
        server: FakeRegistaServer
        version_id: str
        settings: AgentSettings

        def cli(self, *args: str, check: bool = True) -> "subprocess.CompletedProcess[str]":
            env = {k: v for k, v in os.environ.items() if not k.startswith("REGISTA_")}
            return run([str(self.python), "-m", "regista_agent", *args], check=check, env=env)

        def probe(self, **params: Any) -> tuple[str, dict[str, Any], list[str], str]:
            """Run the probe through the services. (how it ended, the report, its log, job id)"""
            job_id = self.server.submit(self.version_id, "isolation_probe", "1.0.0", **params)
            action, body = self.server.result(job_id)
            return action, body, self.server.log_lines(job_id), job_id

    def _kill_leftovers() -> None:
        for name in SERVICES:
            if service_state(name) != "MISSING":
                sc("stop", name, check=False)
                time.sleep(1)
                sc("delete", name, check=False)
        run(["schtasks.exe", "/Delete", "/TN", "Regista\\RobotHost", "/F"], check=False)
        for folder in (HOME, APP):
            shutil.rmtree(folder, ignore_errors=True)

    def _set_service_environment(name: str, values: list[str]) -> None:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{name}",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "Environment", 0, winreg.REG_MULTI_SZ, values)

    @pytest.fixture(scope="module")
    def machine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Machine]:
        _kill_leftovers()
        work = tmp_path_factory.mktemp("real-host")
        uv_bin = uv.find_uv_bin()

        # --- the program: in a folder only administrators can change -------------------------
        APP.mkdir(parents=True)
        # A Python managed by uv, inside the program folder: the runner's own Python lives in a
        # folder every user can write to, and its venv has a launcher (the installer refuses both).
        env = {
            **os.environ,
            "UV_PYTHON_INSTALL_DIR": str(APP / "python"),
            "UV_PYTHON_PREFERENCE": "only-managed",
        }
        version = ".".join(str(n) for n in sys.version_info[:3])
        run([uv_bin, "venv", "--python", version, str(APP / "venv")], env=env)
        python = APP / "venv" / "Scripts" / "python.exe"
        run(
            [
                uv_bin,
                "pip",
                "install",
                "--python",
                str(python),
                str(REPO / "libs" / "pkg"),
                str(REPO / "agent"),
            ],
            env=env,
        )
        exact = run(
            [str(python), "-c", "import platform; print(platform.python_version())"]
        ).stdout.strip()
        run(["icacls.exe", str(APP), "/inheritance:r"])
        run(
            [
                "icacls.exe",
                str(APP),
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
                "*S-1-5-32-545:(OI)(CI)RX",
            ]
        )

        # --- a signing key the agents of the test trust, a signed package of the probe ------
        tenant = uuid.uuid4()
        key = new_key(work / "keys")
        trusted = APP / "trusted.json"
        shutil.copy(key.keys_file, trusted)
        out = work / "dist"
        built = pack_build.build(
            PROBE, version="1.0.0", clients=[str(tenant)], key=key.private, key_id=key.key_id,
            out_dir=out, python=exact,
        )  # fmt: skip

        server = FakeRegistaServer(tenant_id=tenant)
        server.start()
        version_id = str(uuid.uuid4())
        package = built[0]
        offer = {
            "version_id": version_id,
            "version": "1.0.0",
            "package_name": "isolation_probe",
            "sha256": package.sha256,
            "size_bytes": package.size,
            "key_id": key.key_id,
            "signature_doc": package.signature_path.read_text("utf-8"),
        }
        server.publish(version_id, offer, package.package_path.read_bytes())

        import zipfile

        with zipfile.ZipFile(package.package_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        server.runtimes = [
            {
                "package_name": "isolation_probe",
                "version": "1.0.0",
                "python": manifest["python"],
                "playwright": manifest.get("playwright"),
                "chromium_revision": manifest.get("chromium_revision"),
            }
        ]

        settings = AgentSettings()
        machine = Machine(python, server, version_id, settings)

        # --- install it the way a customer would -----------------------------------------------
        machine.cli("enroll", "--url", server.url, "--key", "rgk_ci-test")
        settings = AgentSettings()
        machine.settings = settings
        assert settings.robot_account == r"NT SERVICE\RegistaRobot"
        machine.cli("setup", "--from-server")
        machine.cli("allow", "isolation_probe")
        install = machine.cli("service", "install")
        assert "AVISO" not in install.stdout, install.stdout  # the program is in a safe folder
        for name in SERVICES:
            _set_service_environment(
                name, ["REGISTA_ENVIRONMENT=dev", f"REGISTA_DEV_TRUSTED_KEYS={trusted}"]
            )
        sc("start", "RegistaRobot")
        sc("start", "RegistaAgent")
        assert server.wait_heartbeat(120), "the agent service never said it was alive"

        try:
            yield machine
        finally:
            tail = ""
            log = HOME / "logs" / "agent.log"
            if log.exists():
                tail = log.read_text("utf-8", errors="replace")[-6000:]
            print("--- agent.log ---\n" + tail)
            machine.cli("service", "uninstall", check=False)
            server.stop()
            shutil.rmtree(APP, ignore_errors=True)
            shutil.rmtree(HOME, ignore_errors=True)

    # --- the services and the permissions ------------------------------------------------------

    def test_the_services_run_as_their_own_virtual_accounts(machine: Machine) -> None:
        for name, account in (
            ("RegistaAgent", r"NT SERVICE\RegistaAgent"),
            ("RegistaRobot", r"NT SERVICE\RegistaRobot"),
        ):
            assert service_state(name) == "RUNNING"
            user = psutil.Process(service_pid(name)).username()
            assert user.upper() == account.upper()
        assert service_pid("RegistaAgent") != service_pid("RegistaRobot")

    def test_install_is_idempotent_and_status_tells_the_truth(machine: Machine) -> None:
        again = machine.cli("service", "install")
        assert "atualizado" in again.stdout
        status = machine.cli("service", "status").stdout
        assert r"NT SERVICE\RegistaAgent" in status and r"NT SERVICE\RegistaRobot" in status
        assert "só Administradores e SYSTEM alteram" in status

    def test_the_permission_matrix_on_disk_is_the_documented_one(machine: Machine) -> None:
        agent_sid = _windows.resolve_sid(r"NT SERVICE\RegistaAgent")
        robot_sid = _windows.resolve_sid(r"NT SERVICE\RegistaRobot")
        assert agent_sid != robot_sid
        assert layout.check(AgentSettings(), agent_sid, robot_sid) == []

    def test_diagnose_sees_the_agent_and_the_host_as_installed(machine: Machine) -> None:
        from regista_agent import diagnose

        statuses = {c.name: c.status for c in diagnose.host_checks(AgentSettings())}
        assert statuses["Serviço do agente"] == "ok", statuses
        assert statuses["Hospedeiro do robô"] == "ok", statuses
        assert statuses["Canal do hospedeiro"] == "ok", statuses
        # ("Arquivos do programa" looks at the interpreter running this test, not the services'.)

    # --- the pipe ------------------------------------------------------------------------------

    def test_the_pipe_refuses_anyone_but_the_robot_account(machine: Machine) -> None:
        # This process is an administrator, not the robot: the pipe's ACL does not name it.
        with pytest.raises(OSError):
            winpipe.connect("regista-robot-host", timeout=3)

    def test_only_one_pipe_with_that_name_can_exist(machine: Machine) -> None:
        robot_sid = _windows.resolve_sid(r"NT SERVICE\RegistaRobot")
        with pytest.raises(OSError):
            winpipe.PipeServer("regista-robot-host", [robot_sid])

    # --- the probe: what a robot can and cannot do -----------------------------------------------

    def test_a_robot_cannot_reach_what_it_must_not_and_runs_normally(machine: Machine) -> None:
        key_bytes = (HOME / "keys" / "machine.key").read_bytes()
        action, body, lines, _job = machine.probe(browser=True, junction=True)
        text = "\n".join(lines)
        assert "PERMITIDA" not in text, text
        assert "FALHOU" not in text, text
        assert "NEGADA" in text and "título da página: ok" in text, text
        assert action == "complete", (body, text)
        assert "RESULTADO proibidas_permitidas=0 necessarias_negadas=0" in text
        # The screenshot arrived, the junction to the key folder did not become one.
        uploads = machine.server.uploads
        assert len(uploads) == 1 and uploads[0].startswith(b"\x89PNG")
        assert all(key_bytes not in data for data in uploads)
        assert (HOME / "keys" / "machine.key").read_bytes() == key_bytes, "the key is intact"
        assert not any((HOME / "runs").iterdir()), "the run folder is gone"

    def test_the_robot_runs_as_the_robot_account_and_not_as_the_agent(machine: Machine) -> None:
        _action, _body, lines, _job = machine.probe()
        who = next(line for line in lines if "executando como" in line).lower()
        assert "registarobot" in who and "registaagent" not in who, who

    def test_what_a_robot_writes_in_the_registry_does_not_reach_the_next_run(
        machine: Machine,
    ) -> None:
        action, _body, lines, _job = machine.probe(phase="persist")
        assert action == "complete", lines
        action, _body, lines, _job = machine.probe(phase="verify")
        text = "\n".join(lines)
        assert action == "complete" and "PERMITIDA" not in text and "afetou" not in text, text

    # --- local users ---------------------------------------------------------------------------

    def _net(*args: str) -> None:
        run(["net", *args])

    @contextmanager
    def _local_user(name: str, password: str) -> Iterator[None]:
        _net("user", name, password, "/add")
        try:
            yield
        finally:
            run(["net", "user", name, "/delete"], check=False)

    @contextmanager
    def _as(user: str, password: str) -> Iterator[None]:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32.LogonUserW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]  # fmt: skip
        advapi32.ImpersonateLoggedOnUser.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        if not advapi32.LogonUserW(user, ".", password, 3, 0, ctypes.byref(token)):
            raise OSError(f"LogonUser falhou ({ctypes.get_last_error()})")
        try:
            if not advapi32.ImpersonateLoggedOnUser(token):
                raise OSError(f"ImpersonateLoggedOnUser falhou ({ctypes.get_last_error()})")
            try:
                yield
            finally:
                advapi32.RevertToSelf()
        finally:
            kernel32.CloseHandle(token)

    def test_an_ordinary_local_user_reaches_nothing_of_the_agents_folder(machine: Machine) -> None:
        user = "rgu" + uuid.uuid4().hex[:8]
        password = secrets.token_urlsafe(6) + "aA1!"
        with _local_user(user, password), _as(user, password):
            for name in ("keys", "packages", "logs", "uv-cache", "runs", "python", "browsers"):
                with pytest.raises(PermissionError):
                    list(os.scandir(HOME / name))
            for name in ("agent.toml", r"keys\machine.key", r"keys\identity.json"):
                with pytest.raises(PermissionError), open(HOME / name, "rb"):
                    pass
            with pytest.raises(PermissionError):
                list(os.scandir(HOME))

    # --- stopping and losing the host ----------------------------------------------------------

    def _wait_job_started(machine: Machine, job_id: str) -> int:
        assert machine.server.wait_log(job_id, "filho "), "the probe never reported its child"
        line = next(m for m in machine.server.log_lines(job_id) if m.startswith("PROBE filho"))
        return int(line.split()[-1])

    def test_cancelling_a_run_takes_the_robot_and_its_child_down(machine: Machine) -> None:
        job_id = machine.server.submit(machine.version_id, "isolation_probe", "1.0.0", phase="wait")
        child = _wait_job_started(machine, job_id)
        machine.server.cancellations.add(job_id)
        try:
            action, body = machine.server.result(job_id, 90)
        finally:
            machine.server.cancellations.discard(job_id)
        assert action == "fail" and body["error_code"] == "cancelled", body
        wait_until(lambda: not psutil.pid_exists(child), 20, "o filho do robô morrer")
        assert not any((HOME / "runs").iterdir())

    def test_without_the_host_the_run_fails_and_nothing_runs_as_the_agent(machine: Machine) -> None:
        sc("stop", "RegistaRobot")
        wait_until(lambda: service_state("RegistaRobot") == "STOPPED", 60, "o hospedeiro parar")
        try:
            action, body, lines, _job = machine.probe()
            assert action == "fail" and body["error_code"] == "robot_host_unavailable", body
            assert not body.get("message"), "no free text for the panel"
            assert not any("PROBE" in line for line in lines), "the robot never ran"
        finally:
            sc("start", "RegistaRobot")
        action, _body, lines, _job = machine.probe()
        assert action == "complete", lines

    def test_losing_the_host_in_the_middle_of_a_run_kills_the_robots(machine: Machine) -> None:
        job_id = machine.server.submit(machine.version_id, "isolation_probe", "1.0.0", phase="wait")
        child = _wait_job_started(machine, job_id)
        host = service_pid("RegistaRobot")
        run(["taskkill.exe", "/F", "/PID", str(host)])
        action, body = machine.server.result(job_id, 90)
        assert action == "fail" and body["error_code"] == "robot_host_unavailable", body
        wait_until(lambda: not psutil.pid_exists(child), 20, "o filho morrer com o hospedeiro")
        # The service manager brings the host back by itself (the failure actions), and runs work.
        wait_until(lambda: service_state("RegistaRobot") == "RUNNING", 90, "o hospedeiro voltar")
        action, _body, lines, _job = machine.probe()
        assert action == "complete", lines

    # --- the end -------------------------------------------------------------------------------

    def test_uninstall_leaves_nothing_behind(machine: Machine) -> None:
        machine.cli("service", "uninstall")
        for name in SERVICES:
            assert service_state(name) == "MISSING"
        again = machine.cli("service", "uninstall")
        assert "nada a remover" in again.stdout
