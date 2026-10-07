"""A signed run from start to finish on the agent (M4): the checks in their order, each refusal
with its code and reason, the runtime and the environment, the local list and the kill switch.

Environments are made by the real uv, offline, from the Python of the test run (the stand-in for
the runtime that `regista-agent setup` installs on a customer's machine)."""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from regista_agent import environment, layout, policy, runtime
from regista_agent.config import AgentSettings
from regista_agent.jobapi import Assignment
from regista_agent.jobs import JobExecutor, JobState
from regista_agent.keystore import KeyStore

from .jobs_support import FakeApi
from .package_support import Built, TestKey, build, new_key
from .support import TENANT_ID

PACKAGE = "fake_bot"
SID = "S-1-5-21-1-2-3-1000"
VERSION_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def key(tmp_path: Path) -> TestKey:
    return new_key(tmp_path / "keys")


@pytest.fixture
def no_runtime_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ACL of the runtime folders has its own test on Windows; here it is stood in for."""
    from regista_agent import layout

    monkeypatch.setattr(layout, "apply", lambda settings, agent_sid, robot_sid: [])


REAL_LOCK_APPLIES = layout.lock_applies


@pytest.fixture
def agent_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Writing ACLs (also for the run folders) has its own tests; here a production-mode run on a
    # non-elevated Windows console would lock out the very person running the tests.
    monkeypatch.setattr(layout, "lock_applies", lambda settings: False)
    keys = home / "keys"
    keys.mkdir(parents=True)
    (keys / "identity.json").write_text(json.dumps({"tenant_id": str(TENANT_ID)}), "utf-8")
    return home


def settings(**over: Any) -> AgentSettings:
    values: dict[str, Any] = {
        "environment": "dev",
        "dev_python": Path(sys.executable),
        "allowed_bots": [PACKAGE],
        "cancel_grace_seconds": 1,
        "log_flush_seconds": 0.1,
        "poll_wait_seconds": 0,
        "job_priority": "normal",
        **over,
    }
    return AgentSettings(**values)


def assignment(version: str | None = "1.0.0", **over: Any) -> Assignment:
    fields: dict[str, Any] = {
        "job_id": "00000000-0000-4000-8000-0000000000aa",
        "short_code": "exec-abc123",
        "package_name": PACKAGE,
        "params": {},
        "timeout_seconds": 60,
        "bot_version_id": VERSION_ID,
        "version": version,
    }
    fields.update(over)
    return Assignment(**fields)


def serve(api: FakeApi, built: Built) -> None:
    api.offers[VERSION_ID] = built.offer(VERSION_ID)
    api.package_files[built.url] = built.data


def execute(cfg: AgentSettings, api: FakeApi, key: TestKey, job: Assignment | None = None) -> None:
    executor = JobExecutor(
        cfg,
        api,
        JobState(),
        threading.Event(),
        base_env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        },
        keys=key.trusted(),
    )
    executor.execute(job or assignment())


def outcome(api: FakeApi) -> tuple[str, str | None]:
    """(how it ended, the reason) of the single run the fake saw."""
    if api.completed:
        return "completed", None
    assert len(api.failed) == 1, api.failed
    return api.failed[0][1], api.failed_reasons[api.failed[0][0]]


# --- the good path ----------------------------------------------------------------------------


def test_a_signed_package_runs_in_an_environment_made_for_this_run(
    agent_home: Path, key: TestKey
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    serve(api, built)
    cfg = settings()
    execute(cfg, api, key)

    assert outcome(api) == ("completed", None)
    assert "hello from a signed robot" in api.messages
    assert (cfg.packages_dir / PACKAGE / f"{built.sha256}.rgpkg").is_file()
    assert not any(cfg.runs_dir.iterdir()), "the run folder, environment included, is gone"


def test_the_second_run_reuses_the_cache_but_not_the_environment(
    agent_home: Path, key: TestKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    serve(api, built)
    cfg = settings()
    made: list[Path] = []
    real = environment.build

    def spy(*args: Any, **kwargs: Any) -> Path:
        made.append(kwargs["venv"])
        return real(*args, **kwargs)

    monkeypatch.setattr(environment, "build", spy)
    execute(cfg, api, key)
    api.failed.clear()
    execute(cfg, api, key)
    assert api.completed == [assignment().job_id, assignment().job_id]
    assert len(made) == 2 and made[0] != made[1], "a new environment each time"
    assert api.downloads == [built.url], "but the package was not downloaded again"


# --- every refusal, with its code and reason --------------------------------------------------


def test_a_package_of_another_client_never_runs(agent_home: Path, key: TestKey) -> None:
    built = build(key, tenant_id=uuid.uuid4())
    api = FakeApi()
    serve(api, built)
    execute(settings(), api, key)
    assert outcome(api) == ("package_invalid", "wrong_client")
    assert api.downloads == [] and "hello from a signed robot" not in api.messages
    assert any("wrong_client" in m for m in api.messages), "the detail goes to the run's logs"
    assert api.failed[0][2] == "", "and no free text goes in the report"


def test_a_tampered_download_never_runs(agent_home: Path, key: TestKey) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    serve(api, built)
    tampered = bytearray(built.data)
    tampered[len(tampered) // 2] ^= 0xFF
    api.package_files[built.url] = bytes(tampered)
    execute(settings(), api, key)
    assert outcome(api) == ("package_invalid", "hash_mismatch")
    assert "hello from a signed robot" not in api.messages


def test_a_signature_from_a_key_the_agent_does_not_trust_never_runs(
    agent_home: Path, key: TestKey, tmp_path: Path
) -> None:
    other = new_key(tmp_path / "other-keys")
    built = build(other, tenant_id=TENANT_ID)
    api = FakeApi()
    serve(api, built)
    execute(settings(), api, key)  # trusts `key`, not `other`
    assert outcome(api) == ("package_invalid", "unknown_key")


def test_a_package_for_another_robot_or_version_never_runs(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, build(key, tenant_id=TENANT_ID, version="2.0.0"))
    execute(settings(), api, key, assignment("1.0.0"))
    assert outcome(api) == ("package_invalid", "wrong_version")


def test_a_hostile_zip_never_runs_and_leaves_nothing_behind(
    agent_home: Path, key: TestKey, tmp_path: Path
) -> None:
    built = build(key, tenant_id=TENANT_ID, members={"../../escaped.py": b"boom"})
    api = FakeApi()
    serve(api, built)
    cfg = settings()
    execute(cfg, api, key)
    assert outcome(api) == ("package_invalid", "unsafe_archive")
    assert not list(tmp_path.rglob("escaped.py")) and not list(agent_home.rglob("escaped.py"))
    assert not list(cfg.runs_dir.glob("*")), "no run folder stays"


def test_a_robot_that_is_not_on_the_local_list_does_not_run(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, build(key, tenant_id=TENANT_ID))
    execute(settings(allowed_bots=[]), api, key)  # an empty list releases nothing
    assert outcome(api) == ("robot_not_allowed", None)
    assert api.downloads == [], "not even downloaded"
    api.failed.clear()
    execute(settings(allowed_bots=["another_robot"]), api, key)
    assert outcome(api) == ("robot_not_allowed", None)


def test_a_machine_that_does_not_know_its_client_refuses_every_package(
    agent_home: Path, key: TestKey
) -> None:
    (agent_home / "keys" / "identity.json").unlink()
    api = FakeApi()
    serve(api, build(key, tenant_id=TENANT_ID))
    execute(settings(), api, key)
    assert outcome(api)[0] == "internal" and api.downloads == []


def test_without_the_runtime_the_run_fails_and_nothing_is_downloaded_to_get_it(
    agent_home: Path, key: TestKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    serve(api, build(key, tenant_id=TENANT_ID))
    called: list[object] = []
    monkeypatch.setattr(environment, "default_runner", lambda *a, **k: called.append(a))
    execute(settings(dev_python=None), api, key)  # no stand-in: look under python/ (empty)
    assert outcome(api) == ("runtime_missing", None)
    assert called == [], "uv was never run: the agent downloads nothing by itself"
    assert any("regista-agent setup" in m for m in api.messages)


def test_a_failing_uv_is_environment_failed(
    agent_home: Path, key: TestKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    built = build(key, tenant_id=TENANT_ID)
    serve(api, built)
    monkeypatch.setattr(
        environment,
        "default_runner",
        lambda args, env: subprocess.CompletedProcess(args, 2, "", "boom"),
    )
    cfg = settings()
    execute(cfg, api, key)
    assert outcome(api) == ("environment_failed", None)
    assert not list(cfg.runs_dir.glob("*")), "no half-made environment stays"


def test_a_run_without_a_version_is_refused_by_an_agent_without_the_dev_flag(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    execute(settings(), api, key, assignment(bot_version_id=None, version=None))
    assert outcome(api) == ("robot_not_found", None)


def test_production_never_runs_a_folder_even_with_the_flag_set(
    agent_home: Path, key: TestKey, tmp_path: Path
) -> None:
    (tmp_path / "bots" / PACKAGE).mkdir(parents=True)
    (tmp_path / "bots" / PACKAGE / "main.py").write_text("print('unsigned')\n", "utf-8")
    api = FakeApi()
    cfg = settings(environment="prod", dev_unsigned=True, dev_bots_dir=tmp_path / "bots")
    execute(cfg, api, key, assignment(bot_version_id=None, version=None))
    assert outcome(api) == ("robot_not_found", None)
    assert "unsigned" not in api.messages


# --- the kill switch --------------------------------------------------------------------------


def test_with_the_kill_switch_on_no_run_is_asked_for(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    api.queue = [assignment()]
    cfg = settings()
    policy.pause(cfg)
    stop = threading.Event()
    executor = JobExecutor(cfg, api, JobState(), stop, keys=key.trusted())
    worker = threading.Thread(target=executor.loop, daemon=True)
    worker.start()
    time.sleep(0.5)
    stop.set()
    worker.join(10)
    assert api.queue and api.started == [], "the run waited"
    assert policy.is_paused(cfg)
    policy.resume(cfg)
    assert not policy.is_paused(cfg)


# --- the local list and the switch, from the command line -------------------------------------


def test_allow_and_disallow_change_only_the_local_list(agent_home: Path) -> None:
    cfg = settings(allowed_bots=[])
    assert policy.allow(cfg, "robo_b") == ["robo_b"]
    assert policy.allow(cfg, "robo_a") == ["robo_a", "robo_b"]
    assert policy.allow(cfg, "robo_a") == ["robo_a", "robo_b"]  # idempotent
    assert AgentSettings().allowed_bots == ["robo_a", "robo_b"], "it is in agent.toml"
    assert policy.disallow(cfg, "robo_a") == ["robo_b"]
    for bad in ("../x", "Robo", "", "a b"):
        with pytest.raises(Exception, match="nome de pacote"):
            policy.allow(cfg, bad)


def test_the_commands_need_an_elevated_console_outside_development(
    agent_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from regista_agent.errors import AgentError

    monkeypatch.setattr(policy, "is_elevated", lambda: False)
    with pytest.raises(AgentError, match="Administrador"):
        policy.require_elevation(settings(environment="prod"), "O comando allow")
    policy.require_elevation(settings(environment="dev"), "O comando allow")  # dev: no admin
    monkeypatch.setattr(policy, "is_elevated", lambda: True)
    policy.require_elevation(settings(environment="prod"), "O comando allow")


def test_the_cli_allows_pauses_and_resumes(
    agent_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from regista_agent.cli import main

    monkeypatch.setenv("REGISTA_ENVIRONMENT", "dev")
    for argv, expect in (
        (["allow", "meu_robo"], "liberado"),
        (["pause"], "Kill switch ligado"),
        (["resume"], "desligado"),
        (["disallow", "meu_robo"], "removido"),
    ):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 0
        assert expect in capsys.readouterr().out
    assert not policy.is_paused(AgentSettings())


# --- the runtime ------------------------------------------------------------------------------


def _fake_python(cfg: AgentSettings, version: str) -> Path:
    folder = cfg.python_dir / f"cpython-{version}-windows-x86_64-none"
    exe = folder / ("python.exe" if sys.platform == "win32" else "bin/python3")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    return exe


def test_the_runtime_is_looked_for_where_setup_puts_it(agent_home: Path, key: TestKey) -> None:
    from regista_pkg import Manifest

    cfg = settings(dev_python=None)
    manifest = Manifest(
        tenant_id=str(TENANT_ID),
        package_name=PACKAGE,
        version="1.0.0",
        python="3.13.5",
        playwright="1.55.0",
        chromium_revision="1187",
    )
    with pytest.raises(runtime.RuntimeMissing, match=r"Python 3.13.5"):
        runtime.require(cfg, manifest)
    exe = _fake_python(cfg, "3.13.5")
    with pytest.raises(runtime.RuntimeMissing, match="Chromium 1187"):
        runtime.require(cfg, manifest)
    (cfg.browsers_dir / "chromium-1187").mkdir(parents=True)
    assert runtime.require(cfg, manifest) == exe
    assert runtime.installed(cfg) == (["3.13.5"], ["1187"])
    # Another patch version of Python is not this Python.
    other = manifest.__class__(**{**manifest.to_dict(), "python": "3.13.6"})
    with pytest.raises(runtime.RuntimeMissing, match=r"Python 3\.13\.6"):
        runtime.require(cfg, other)


def test_the_robot_gets_the_browsers_folder_of_the_agent_not_the_one_it_inherited(
    agent_home: Path,
) -> None:
    from regista_agent import robot

    env = robot.build_env(
        base={"PLAYWRIGHT_BROWSERS_PATH": r"C:\Users\someone\ms-playwright", "PATH": "p"},
        job_id="j",
        params_json="{}",
        artifacts_dir=Path("a"),
        cancel_file=Path("c"),
        temp_dir=Path("t"),
        browsers_path=Path(r"C:\ProgramData\Regista\browsers"),
    )
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == r"C:\ProgramData\Regista\browsers"
    bare = robot.build_env(
        base={"PLAYWRIGHT_BROWSERS_PATH": "inherited"},
        job_id="j",
        params_json="{}",
        artifacts_dir=Path("a"),
        cancel_file=Path("c"),
        temp_dir=Path("t"),
    )
    assert "PLAYWRIGHT_BROWSERS_PATH" not in bare


def test_setup_installs_exactly_the_versions_asked_and_checks_the_revision(
    agent_home: Path, no_runtime_acl: None
) -> None:
    cfg = settings(dev_python=None)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(args: list[str], env: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, dict(env)))
        if args[1:3] == ["python", "install"]:
            _fake_python(cfg, args[3])
        if args[1:2] == ["venv"]:
            exe = environment.venv_python(Path(args[-1]))
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", encoding="utf-8")
        if args[-3:] == ["playwright", "install", "chromium"]:
            (cfg.browsers_dir / "chromium-1187").mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    needs = [
        runtime.SimpleNeed("3.13.5", "1.55.0", "1187"),
        runtime.SimpleNeed("3.13.5", None, None),
    ]
    said: list[str] = []
    plan = runtime.run_setup(cfg, needs, agent_sid=SID, say=said.append, runner=runner)
    assert plan.pythons == ("3.13.5",) and plan.browsers == (("1.55.0", "3.13.5", "1187"),)
    install = next(c for c in calls if c[0][1:3] == ["python", "install"])
    assert install[0][3] == "3.13.5", "an exact version, never 'latest'"
    assert install[1]["UV_PYTHON_INSTALL_DIR"] == str(cfg.python_dir)
    pip = next(c for c in calls if c[0][1:3] == ["pip", "install"])
    assert "playwright==1.55.0" in pip[0]
    browsers = next(c for c in calls if c[0][-3:] == ["playwright", "install", "chromium"])
    assert browsers[1]["PLAYWRIGHT_BROWSERS_PATH"] == str(cfg.browsers_dir)
    assert not list(cfg.home.glob("setup-*")), "the scratch environment is removed"

    calls.clear()  # nothing to do the second time
    runtime.run_setup(cfg, needs, agent_sid=SID, say=said.append, runner=runner)
    assert calls == []


def test_setup_refuses_a_chromium_that_is_not_the_one_the_package_declares(
    agent_home: Path, no_runtime_acl: None
) -> None:
    from regista_agent.errors import AgentError

    cfg = settings(dev_python=None)

    def runner(args: list[str], env: Any) -> subprocess.CompletedProcess[str]:
        if args[1:3] == ["python", "install"]:
            _fake_python(cfg, args[3])
        if args[1:2] == ["venv"]:
            exe = environment.venv_python(Path(args[-1]))
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", encoding="utf-8")
        if args[-3:] == ["playwright", "install", "chromium"]:
            (cfg.browsers_dir / "chromium-9999").mkdir(parents=True)  # another revision
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(AgentError, match="1187"):
        runtime.run_setup(
            cfg,
            [runtime.SimpleNeed("3.13.5", "1.55.0", "1187")],
            agent_sid=SID,
            say=print,
            runner=runner,
        )


def test_the_environment_is_installed_from_the_package_wheels_offline_hashed_and_without_cache(
    agent_home: Path, tmp_path: Path
) -> None:
    cfg = settings()
    calls: list[list[str]] = []

    def runner(args: list[str], env: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "venv":
            exe = environment.venv_python(Path(args[-1]))
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", encoding="utf-8")
        assert env["UV_PYTHON_DOWNLOADS"] == "never" and env["UV_CACHE_DIR"] == str(
            cfg.uv_cache_dir
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    lock = tmp_path / "requirements.lock"
    lock.write_text("dep==1.0 --hash=sha256:" + "a" * 64 + "\n", "utf-8")
    python = environment.build(
        cfg,
        base_python=Path(sys.executable),
        wheels=tmp_path / "wheels",
        lock=lock,
        venv=tmp_path / "venv",
        runner=runner,
    )
    assert python.is_file()
    install = next(c for c in calls if c[1:3] == ["pip", "install"])
    # No cache: uv does not recheck the hash of what it takes from it. Copy: a hardlink would share
    # the ACL of the cache.
    for flag in ("--offline", "--no-index", "--require-hashes", "--no-cache", "--link-mode=copy"):
        assert flag in install
    assert install[install.index("--find-links") + 1] == str(tmp_path / "wheels")


def test_the_identity_reader_is_what_the_executor_uses(agent_home: Path) -> None:
    assert KeyStore(agent_home / "keys", agent_account=None).read_tenant_id() == TENANT_ID


def test_a_run_taken_just_as_the_kill_switch_went_on_is_given_back_untouched(
    agent_home: Path, key: TestKey
) -> None:
    """The switch can go on while a long poll waits; the run that comes back anyway is released
    to the queue, never started."""
    cfg = settings()
    stop = threading.Event()

    class PausingApi(FakeApi):
        def next_job(self, wait: int) -> Assignment | None:
            job = super().next_job(wait)
            if job is not None:
                policy.pause(cfg)  # it goes on while the request was waiting
            return job

        def release(self, job_id: str) -> None:
            super().release(job_id)
            stop.set()

    api = PausingApi()
    api.queue = [assignment()]
    executor = JobExecutor(cfg, api, JobState(), stop, keys=key.trusted())
    worker = threading.Thread(target=executor.loop, daemon=True)
    worker.start()
    worker.join(15)
    assert api.released == [assignment().job_id]
    assert api.started == [] and api.completed == [] and api.failed == []
    policy.resume(cfg)


def test_setup_with_a_folder_of_wheels_never_asks_the_pypi_for_playwright(
    agent_home: Path, no_runtime_acl: None, tmp_path: Path
) -> None:
    cfg = settings(dev_python=None)
    calls: list[list[str]] = []

    def runner(args: list[str], env: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["python", "install"]:
            _fake_python(cfg, args[3])
        if args[1:2] == ["venv"]:
            exe = environment.venv_python(Path(args[-1]))
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", encoding="utf-8")
        if args[-3:] == ["playwright", "install", "chromium"]:
            (cfg.browsers_dir / "chromium-1187").mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    runtime.run_setup(
        cfg,
        [runtime.SimpleNeed("3.13.5", "1.55.0", "1187")],
        agent_sid=SID,
        say=print,
        runner=runner,
        wheels=tmp_path / "wheels",
    )
    pip = next(c for c in calls if c[1:3] == ["pip", "install"])
    for flag in ("--offline", "--no-index"):
        assert flag in pip
    assert pip[pip.index("--find-links") + 1] == str(tmp_path / "wheels")


def test_uv_gets_the_proxy_and_certificates_the_agent_is_configured_with(
    agent_home: Path, tmp_path: Path
) -> None:
    ca = tmp_path / "company-ca.pem"
    env = environment.uv_env(settings(proxy="http://proxy.corp:3128", ca_bundle=ca))
    assert env["HTTPS_PROXY"] == "http://proxy.corp:3128" and env["SSL_CERT_FILE"] == str(ca)
    assert "REGISTA_MASTER_KEY" not in env and "REGISTA_HOME" not in env


def test_a_development_setup_without_elevation_does_not_lock_the_folders(
    agent_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ACLs would shut out the very person running a dev agent."""
    if sys.platform != "win32":
        pytest.skip("the ACL is a Windows thing")
    monkeypatch.setattr(layout, "lock_applies", REAL_LOCK_APPLIES)
    applied: list[object] = []

    def record(cfg: object, agent_sid: str, robot_sid: str) -> list[str]:
        applied.append(cfg)
        return []

    monkeypatch.setattr(layout, "apply", record)
    monkeypatch.setattr(policy, "is_elevated", lambda: False)
    runtime.prepare_folders(settings(), SID, SID)
    assert applied == []
    runtime.prepare_folders(settings(environment="prod"), SID, SID)  # production always locks
    assert len(applied) == 1
    monkeypatch.setattr(policy, "is_elevated", lambda: True)
    runtime.prepare_folders(settings(), SID, SID)  # an elevated dev console too
    assert len(applied) == 2
