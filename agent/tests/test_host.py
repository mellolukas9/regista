"""Running robots through the robot host: the agent's launcher, the protocol on a real (in-memory)
connection and the host itself starting real processes (docs/adr/0022).

The pipe is stood in for by memory; the Windows tests with the real pipe, the real services and the
real accounts are in `test_windows_host.py` and run in the Windows job of the CI."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from regista_agent import hostproto, launcher, rundir
from regista_agent.config import AgentSettings
from regista_agent.host import HostCore, TreeContainment
from regista_agent.hostproto import RunSpec
from regista_agent.jobs import JobExecutor, JobState

from .host_support import Rig, memory_pair, rig, wait_for
from .jobs_support import FIXTURE_BOTS, FakeApi
from .package_support import TestKey, build
from .support import TENANT_ID
from .test_signed_runs import assignment, outcome, serve, settings

FAKE_BOT = (FIXTURE_BOTS / "fake_bot" / "main.py").read_bytes()


def fake_package(key: TestKey) -> Any:
    return build(key, tenant_id=TENANT_ID, members={"bot/main.py": FAKE_BOT})


def run_through_host(
    cfg: AgentSettings,
    api: FakeApi,
    key: TestKey,
    host: Rig,
    job: Any = None,
    state: JobState | None = None,
    stop: threading.Event | None = None,
) -> None:
    executor = JobExecutor(
        cfg,
        api,
        state or JobState(),
        stop or threading.Event(),
        base_env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "USERPROFILE": r"C:\Users\the-agent-profile",
            "HOME": "/home/the-agent-profile",
        },
        keys=key.trusted(),
        robot_launcher=host.launcher,
    )
    executor.execute(job or assignment(params={"mode": "ok"}))


# --- the good path -----------------------------------------------------------------------------


def test_a_signed_robot_runs_through_the_host_and_its_output_and_screenshot_arrive(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host)
    assert outcome(api) == ("completed", None)
    assert "passo um" in api.messages and "fim" in api.messages
    assert len(api.uploads) == 1, "the screenshot the robot left in artifacts"
    assert not any(cfg.runs_dir.iterdir()), "the run folder is gone"


def test_a_failing_robot_is_a_robot_failure_not_a_host_failure(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host, assignment(params={"mode": "fail"}))
    assert outcome(api)[0] == "robot_failed"
    assert any("ValueError: boom" in m for m in api.messages)


def test_the_same_host_connection_serves_one_run_after_another(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host)
        run_through_host(cfg, api, key, host)
    assert len(api.completed) == 2


def test_a_robot_gets_an_environment_the_agent_built_and_not_the_agents_profile(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()  # mode None: the service-style profile, redirected into the run folder
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host, assignment(params={"mode": "env"}))
    names = next(m for m in api.messages if m.startswith("NAMES"))
    assert "PYTHONNOUSERSITE" in names and "REGISTA_ARTIFACTS_DIR" in names
    joined = "\n".join(api.messages)
    assert "the-agent-profile" not in joined, "the agent's profile does not reach the robot"


# --- stopping ----------------------------------------------------------------------------------


def test_cancelling_stops_a_cooperative_robot(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    state = JobState()
    with rig(cfg) as host:
        worker = threading.Thread(
            target=run_through_host,
            args=(cfg, api, key, host, assignment(params={"mode": "wait"}), state),
        )
        worker.start()
        assert wait_for(lambda: "robo iniciado" in api.messages)
        state.cancel.set()
        worker.join(30)
    assert outcome(api)[0] == "cancelled"


def test_cancelling_kills_a_stubborn_robot_and_what_it_started(
    agent_home: Path, key: TestKey
) -> None:
    import psutil

    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    state = JobState()
    with rig(cfg) as host:
        worker = threading.Thread(
            target=run_through_host,
            args=(cfg, api, key, host, assignment(params={"mode": "stubborn"}), state),
        )
        worker.start()
        assert wait_for(lambda: any(m.startswith("child") for m in api.messages))
        child = int(next(m for m in api.messages if m.startswith("child")).split()[-1])
        state.cancel.set()
        worker.join(30)
    assert outcome(api)[0] == "cancelled"
    assert wait_for(lambda: not psutil.pid_exists(child), 10), "the browser stand-in is dead too"


def test_a_robot_past_its_deadline_is_stopped(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(
            cfg, api, key, host, assignment(params={"mode": "stubborn"}, timeout_seconds=2)
        )
    assert outcome(api)[0] == "timeout"


# --- no host, no run ---------------------------------------------------------------------------


def test_without_a_host_the_run_fails_and_the_robot_never_runs(
    agent_home: Path, key: TestKey
) -> None:
    """There is no fallback to running the robot as the agent."""
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg, connect=False) as host:
        host.launcher = launcher.HostLauncher(host.channel, wait_seconds=0.5)
        run_through_host(cfg, api, key, host)
    assert outcome(api) == ("robot_host_unavailable", None)
    assert "passo um" not in api.messages and "robo iniciado" not in api.messages
    assert api.failed[0][2] == "", "no free text goes to the panel"
    assert any("hospedeiro" in m for m in api.messages), "the detail is in the run's logs"


def test_a_host_that_never_answers_the_run_is_given_up_on(
    agent_home: Path, key: TestKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "START_TIMEOUT_SECONDS", 0.5)
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg, connect=False) as host:
        _agent_end, host_end = host.connect_host(serve=False)
        host_end.send(hostproto.encode_host(hostproto.Hello(hostproto.PROTOCOL_VERSION)))
        run_through_host(cfg, api, key, host)
    assert outcome(api) == ("robot_host_unavailable", None)


def test_a_host_that_speaks_another_protocol_is_refused(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg, connect=False) as host:
        host.launcher = launcher.HostLauncher(host.channel, wait_seconds=1.0)
        _, host_end = host.connect_host(serve=False)
        host_end.send(b'{"type":"hello","protocol":99}')
        run_through_host(cfg, api, key, host)
    assert outcome(api) == ("robot_host_unavailable", None)


def test_a_host_that_dies_in_the_middle_of_a_run_fails_the_run(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        worker = threading.Thread(
            target=run_through_host,
            args=(cfg, api, key, host, assignment(params={"mode": "stubborn"})),
        )
        worker.start()
        assert wait_for(lambda: any(m.startswith("child") for m in api.messages))
        host.host_ends[0].close()  # the host goes away
        worker.join(30)
    assert outcome(api) == ("robot_host_unavailable", None)


def test_a_host_that_restarts_between_runs_is_waited_for(agent_home: Path, key: TestKey) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host)
        host.host_ends[0].close()  # the first host is gone while idle
        host.connect_host()  # and a new one comes up
        run_through_host(cfg, api, key, host)
    assert len(api.completed) == 2, api.failed


# --- a tampered host ---------------------------------------------------------------------------


def hand_played_host(host: Rig, script: Any) -> threading.Thread:
    """A host played by the test: it greets, then `script(end)` decides what it says."""
    _, host_end = host.connect_host(serve=False)
    host_end.send(hostproto.encode_host(hostproto.Hello(hostproto.PROTOCOL_VERSION)))

    def play() -> None:
        try:
            while True:
                raw = host_end.receive(timeout=10)
                message = hostproto.decode_agent(raw)
                if isinstance(message, hostproto.Run):
                    script(host_end, message.spec)
                    return
        except Exception:  # noqa: S110  (the agent dropped it)
            pass

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread


def started(end: Any, spec: RunSpec) -> None:
    end.send(hostproto.encode_host(hostproto.Started(spec.run_id, 4242)))


@pytest.mark.parametrize(
    "evil",
    [
        pytest.param(lambda s: b'{"type":"get_token"}', id="asks for a token"),
        pytest.param(
            lambda s: b'{"type":"read_file","path":"keys/machine.key"}', id="asks for the key"
        ),
        pytest.param(
            lambda s: b'{"type":"output","run_id":"ffffffffffff","stream":"stdout","line":"x"}',
            id="speaks for another run",
        ),
        pytest.param(lambda s: b'{"type":"shell","cmd":"calc"}', id="unknown type"),
        pytest.param(
            lambda s: b'{"type":"started","run_id":"' + s.run_id.encode() + b'","pid":1}',
            id="started twice",
        ),
        pytest.param(lambda s: b"x" * (hostproto.MAX_MESSAGE_BYTES + 10), id="oversized"),
        pytest.param(lambda s: b"\xff\xfe", id="not json"),
    ],
)
def test_a_tampered_host_that_breaks_the_protocol_costs_it_the_connection_and_the_run(
    agent_home: Path, key: TestKey, evil: Any
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()

    def script(end: Any, spec: RunSpec) -> None:
        started(end, spec)
        end.send(evil(spec))
        time.sleep(5)

    with rig(cfg, connect=False) as host:
        hand_played_host(host, script)
        run_through_host(cfg, api, key, host)
    assert outcome(api) == ("robot_host_unavailable", None)
    assert not any(cfg.runs_dir.iterdir())


def test_a_tampered_host_can_only_lie_about_its_own_run(agent_home: Path, key: TestKey) -> None:
    """The residual risk of the ADR, stated as a test: a host that is under a robot's control can
    make up the output and the result of the one run it was given, and nothing else. It cannot
    make the agent read, send or run anything, and it gets no data from the agent."""
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    seen: list[bytes] = []

    def script(end: Any, spec: RunSpec) -> None:
        started(end, spec)
        end.send(hostproto.encode_host(hostproto.Output(spec.run_id, "stdout", "tudo certo, juro")))
        end.send(hostproto.encode_host(hostproto.Exited(spec.run_id, 0)))
        seen.extend(end.sent)

    with rig(cfg, connect=False) as host:
        player = hand_played_host(host, script)
        run_through_host(cfg, api, key, host)
        player.join(10)
    assert outcome(api) == ("completed", None), "it lied about its own run"
    assert "tudo certo, juro" in api.messages
    assert api.uploads == [] and api.downloads == [fake_package_url(api)], "and did nothing else"


def fake_package_url(api: FakeApi) -> str:
    return api.downloads[0]


def test_the_agent_sends_a_host_only_what_it_needs_to_run_a_robot(
    agent_home: Path, key: TestKey
) -> None:
    api = FakeApi()
    serve(api, fake_package(key))
    cfg = settings()
    with rig(cfg) as host:
        run_through_host(cfg, api, key, host)
        sent = host.connections.empty()  # the connection was consumed by the channel
        assert sent
    # What the agent said is in the host end's inbox history: check it on a hand-played host.
    api2 = FakeApi()
    serve(api2, fake_package(key))
    heard: list[bytes] = []

    def script(end: Any, spec: RunSpec) -> None:
        started(end, spec)
        end.send(hostproto.encode_host(hostproto.Exited(spec.run_id, 0)))

    with rig(cfg, connect=False) as host2:
        _agent_end, host_end = host2.connect_host(serve=False)
        host_end.send(hostproto.encode_host(hostproto.Hello(hostproto.PROTOCOL_VERSION)))

        def play() -> None:
            raw = host_end.receive(timeout=10)
            heard.append(raw)
            message = hostproto.decode_agent(raw)
            assert isinstance(message, hostproto.Run)
            script(host_end, message.spec)

        thread = threading.Thread(target=play, daemon=True)
        thread.start()
        run_through_host(cfg, api2, key, host2)
        thread.join(10)
    text = heard[0].decode()
    for secret in ("rga1.", "rgk_", "machine.key", "Bearer", "REGISTA_MASTER_KEY", "identity.json"):
        assert secret not in text, f"{secret} must not reach the host"
    run = hostproto.decode_agent(heard[0])
    assert isinstance(run, hostproto.Run)
    runs_root = str(cfg.runs_dir)
    for path in (run.spec.python, run.spec.entry, run.spec.cwd):
        assert path.startswith(runs_root), "everything is under the run folders"
    assert not any(k.startswith("REGISTA_") and k not in _ROBOT_VARIABLES for k in run.spec.env)


_ROBOT_VARIABLES = {
    "REGISTA_JOB_ID",
    "REGISTA_JOB_PARAMS",
    "REGISTA_ARTIFACTS_DIR",
    "REGISTA_CANCEL_FILE",
}


# --- the host itself ---------------------------------------------------------------------------


def make_run_dir(cfg: AgentSettings) -> rundir.RunDir:
    run = rundir.create(cfg)
    from regista_agent import environment

    environment.build(
        cfg,
        base_python=Path(sys.executable),
        wheels=run.build,
        lock=run.build / "none.lock",
        venv=run.venv,
    )
    (run.package / "bot").mkdir()
    (run.package / "bot" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return run


def host_conversation(cfg: AgentSettings) -> tuple[Any, threading.Thread]:
    """A real HostCore on one end; the test speaks for the agent on the other."""
    agent_end, host_end = memory_pair()
    core = HostCore(host_end, runs_root=cfg.runs_dir, containment=TreeContainment, environ={})
    thread = threading.Thread(target=lambda: _quiet(core.serve), daemon=True)
    thread.start()
    agent_end.receive(timeout=5)  # the hello
    return agent_end, thread


def _quiet(call: Any) -> None:
    try:
        call()
    except Exception:  # noqa: S110
        pass


def test_the_host_runs_only_from_inside_the_run_folder(agent_home: Path, tmp_path: Path) -> None:
    cfg = settings()
    run = make_run_dir(cfg)
    outside = tmp_path / "evil.py"
    outside.write_text("print('outside')\n")
    end, _ = host_conversation(cfg)
    python = str(run.venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))

    def ask(entry: str, py: str = python, cwd: str | None = None) -> hostproto.HostMessage:
        spec = RunSpec(run.name, py, entry, cwd or str(run.package), {"PATH": ""})
        end.send(hostproto.encode_agent(hostproto.Run(spec)))
        return hostproto.decode_host(end.receive(timeout=10))

    assert isinstance(ask(str(outside)), hostproto.StartFailed), "a script outside the run folder"
    assert isinstance(ask(str(run.package / ".." / ".." / "evil.py")), hostproto.StartFailed)
    assert isinstance(
        ask(str(run.package / "bot" / "main.py"), py=sys.executable), hostproto.StartFailed
    )
    assert isinstance(
        ask(str(run.package / "bot" / "main.py"), cwd=str(tmp_path)), hostproto.StartFailed
    )
    spec = RunSpec(
        "ffffffffffff", python, str(run.package / "bot" / "main.py"), str(run.package), {}
    )
    end.send(hostproto.encode_agent(hostproto.Run(spec)))
    assert isinstance(hostproto.decode_host(end.receive(timeout=10)), hostproto.StartFailed)
    rundir.remove(run)


def test_the_host_answers_a_ping_and_ignores_a_cancel_when_nothing_runs(
    agent_home: Path,
) -> None:
    cfg = settings()
    end, _ = host_conversation(cfg)
    end.send(hostproto.encode_agent(hostproto.Ping()))
    assert isinstance(hostproto.decode_host(end.receive(timeout=5)), hostproto.Pong)
    end.send(hostproto.encode_agent(hostproto.Cancel("0123456789ab", 1)))
    end.send(hostproto.encode_agent(hostproto.Ping()))
    assert isinstance(hostproto.decode_host(end.receive(timeout=5)), hostproto.Pong)


def test_a_robot_dies_with_the_connection_of_its_host(agent_home: Path) -> None:
    import psutil

    cfg = settings()
    run = make_run_dir(cfg)
    (run.package / "bot" / "main.py").write_text(
        "import time\nprint('up', flush=True)\ntime.sleep(120)\n", encoding="utf-8"
    )
    end, thread = host_conversation(cfg)
    python = str(run.venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))
    spec = RunSpec(
        run.name, python, str(run.package / "bot" / "main.py"), str(run.package / "bot"), {}
    )
    end.send(hostproto.encode_agent(hostproto.Run(spec)))
    started_message = hostproto.decode_host(end.receive(timeout=10))
    assert isinstance(started_message, hostproto.Started)
    pid = started_message.pid
    assert psutil.pid_exists(pid)
    end.close()  # the agent's end goes away
    thread.join(10)
    assert wait_for(lambda: not psutil.pid_exists(pid), 10), "no robot outlives its host's link"
    rundir.remove(run)


def test_the_host_cuts_a_long_line_instead_of_breaking_the_protocol(agent_home: Path) -> None:
    cfg = settings()
    run = make_run_dir(cfg)
    (run.package / "bot" / "main.py").write_text(
        "print('x' * 50000, flush=True)\n", encoding="utf-8"
    )
    end, _ = host_conversation(cfg)
    python = str(run.venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))
    spec = RunSpec(
        run.name, python, str(run.package / "bot" / "main.py"), str(run.package / "bot"), {}
    )
    end.send(hostproto.encode_agent(hostproto.Run(spec)))
    conversation = hostproto.HostConversation()
    conversation.accept(b'{"type":"hello","protocol":1}')
    conversation.begin_run(run.name)
    lines = []
    while True:
        message = conversation.accept(end.receive(timeout=15))  # every message passes the checks
        if isinstance(message, hostproto.Output):
            lines.append(message.line)
        if isinstance(message, hostproto.Exited):
            break
    assert lines and len(lines[0].encode()) <= hostproto.MAX_LINE_BYTES
    rundir.remove(run)


def test_the_host_imports_nothing_that_holds_a_secret() -> None:
    """The host has no key, no identity and no way to the server: not even in its imports."""
    code = (
        "import sys, regista_agent.host\n"
        "bad = [m for m in ('regista_agent.keystore', 'regista_agent.transport', "
        "'regista_agent.jobapi', 'regista_agent.enroll', 'regista_agent.trust', "
        "'regista_agent.packages', 'regista_agent.loop', 'regista_agent.jobs', "
        "'regista_agent._windows') if m in sys.modules]\n"
        "print(bad)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", result.stdout


# --- the Job Object (Windows) ------------------------------------------------------------------

_SPAWNER = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(120)\n"
)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
@pytest.mark.parametrize("how", ["terminate", "close"])
def test_a_job_object_kills_the_robot_and_what_it_started(tmp_path: Path, how: str) -> None:
    if sys.platform != "win32":
        pytest.skip("Job Objects are Windows only")
    import psutil

    from regista_agent import winjob

    marker = tmp_path / "child.pid"
    job = winjob.Job()
    popen = job.start([sys.executable, "-c", _SPAWNER, str(marker)])
    assert wait_for(lambda: marker.exists() and marker.read_text() != "")
    child = int(marker.read_text())
    assert psutil.pid_exists(child) and popen.poll() is None
    if how == "terminate":
        job.terminate()
    else:
        job.close()  # the host going away: KILL_ON_JOB_CLOSE
    assert wait_for(lambda: popen.poll() is not None, 10)
    assert wait_for(lambda: not psutil.pid_exists(child), 10), "the child died with the job"
    job.close()
