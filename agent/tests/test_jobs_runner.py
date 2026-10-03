"""The job runner: robot processes, cancellation, logs and screenshots (M3)."""

import json
import os
import re
import threading
import time
from pathlib import Path

import psutil
import pytest

from regista_agent import robot
from regista_agent.errors import MachineRevoked, ServerUnavailable
from regista_agent.jobapi import JobGone
from regista_agent.jobs import JobExecutor, JobState

from .jobs_support import FIXTURE_BOTS, FakeApi, dev_settings, job

PNG_MAGIC = b"\x89PNG"


def _executor(
    api: FakeApi, *, stop: threading.Event | None = None, **settings: object
) -> tuple[JobExecutor, JobState]:
    state = JobState()
    executor = JobExecutor(
        dev_settings(**settings),
        api,
        state,
        stop or threading.Event(),
        base_env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        },
    )
    return executor, state


# --- finding the robot ------------------------------------------------------------------------


def test_the_robot_is_found_only_inside_the_bots_folder() -> None:
    assert (
        robot.resolve_dev_robot(FIXTURE_BOTS, "fake_bot")
        == (FIXTURE_BOTS / "fake_bot" / "main.py").resolve()
    )
    for bad in (
        "missing",
        "..",
        "../fake_bot",
        "fake_bot/../fake_bot",
        "Fake_Bot",
        "",
        "a" * 64,
        "C:\\Windows\\System32",
        "/etc/passwd",
        "fake-bot",
        "fake_bot ",
        "fake_bot\n",
    ):
        assert robot.resolve_dev_robot(FIXTURE_BOTS, bad) is None, repr(bad)
    assert robot.resolve_dev_robot(None, "fake_bot") is None


def test_a_folder_that_links_outside_the_bots_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.py").write_text("print('x')")
    bots = tmp_path / "bots"
    bots.mkdir()
    try:
        (bots / "evil").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert robot.resolve_dev_robot(bots, "evil") is None


# --- the environment of the robot -------------------------------------------------------------


def test_the_environment_is_an_allowlist_and_carries_no_agent_secret(tmp_path: Path) -> None:
    base = {
        "PATH": "/bin",
        "HOME": "/home/x",
        "HTTPS_PROXY": "http://proxy:3128",
        "REGISTA_ENVIRONMENT": "dev",
        "REGISTA_DEV_UNSIGNED": "1",
        "REGISTA_HOME": "/etc/regista",
        "REGISTA_SERVER_URL": "https://regista.example",
        "AWS_SECRET_ACCESS_KEY": "nope",
        "GITHUB_TOKEN": "nope",
        "REGISTA_TOKEN": "rga1.AAAA.BBBB",
    }
    env = robot.build_env(
        base=base,
        job_id="J",
        params_json='{"a": 1}',
        artifacts_dir=tmp_path / "a",
        cancel_file=tmp_path / "c",
        temp_dir=tmp_path / "t",
    )
    assert env["PATH"] == "/bin" and env["HTTPS_PROXY"] == "http://proxy:3128"
    assert {k for k in env if k.startswith("REGISTA_")} == {
        "REGISTA_JOB_ID",
        "REGISTA_JOB_PARAMS",
        "REGISTA_ARTIFACTS_DIR",
        "REGISTA_CANCEL_FILE",
    }
    for leaked in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "REGISTA_HOME", "REGISTA_TOKEN"):
        assert leaked not in env
    assert "rga1" not in json.dumps(env)


def test_the_real_child_sees_only_that_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGISTA_ENVIRONMENT", "dev")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
    api = FakeApi()
    executor = JobExecutor(
        dev_settings(),
        api,
        JobState(),
        threading.Event(),  # the real os.environ as the base
    )
    executor.execute(job("env"))
    assert api.completed, api.failed
    env_line = next(m for m in api.messages if m.startswith("ENV "))
    seen = json.loads(env_line[4:])
    assert set(seen) == {
        "REGISTA_JOB_ID",
        "REGISTA_JOB_PARAMS",
        "REGISTA_ARTIFACTS_DIR",
        "REGISTA_CANCEL_FILE",
    }
    names = json.loads(next(m for m in api.messages if m.startswith("NAMES "))[6:])
    assert "AWS_SECRET_ACCESS_KEY" not in names
    cwd = next(m for m in api.messages if m.startswith("CWD "))[4:]
    assert Path(cwd).resolve() == (FIXTURE_BOTS / "fake_bot").resolve()


# --- a run that works -------------------------------------------------------------------------


def test_a_good_run_ships_logs_uploads_the_screenshot_and_completes() -> None:
    api = FakeApi()
    executor, state = _executor(api)
    executor.execute(job("ok"))

    assert api.started == [job().job_id]
    assert api.completed == [job().job_id] and api.failed == []
    levels = {(line["level"], line["message"]) for line in api.lines}
    assert ("INFO", "robo iniciado") in levels
    assert ("INFO", "passo um") in levels
    assert ("WARN", "passo dois") in levels, "a level name at the start of a line is kept"
    seqs = [line["seq"] for line in api.lines]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT.*\+00:00", line["ts"]) for line in api.lines)

    assert len(api.uploads) == 1 and api.uploads[0][2].startswith(PNG_MAGIC)
    assert api.presigned == [(job().job_id, "image/png", len(api.uploads[0][2]))]
    assert api.confirmed == ["art-1"]
    assert state.current is None


def test_the_working_folder_is_removed_afterwards() -> None:
    import tempfile

    before = {p.name for p in Path(tempfile.gettempdir()).glob("regista-job-*")}
    api = FakeApi()
    _executor(api)[0].execute(job("ok"))
    after = {p.name for p in Path(tempfile.gettempdir()).glob("regista-job-*")}
    assert after <= before


# --- a run that fails -------------------------------------------------------------------------


def test_a_failing_robot_reports_robot_failed_with_a_short_reason() -> None:
    api = FakeApi()
    _executor(api)[0].execute(job("fail"))
    assert api.completed == []
    ((job_id, code, message),) = api.failed
    assert job_id == job().job_id and code == "robot_failed"
    assert "código 2" in message and "ValueError: boom" in message
    by_message = {line["message"]: line["level"] for line in api.lines}
    assert by_message["algo deu errado"] == "ERROR"
    assert by_message["ValueError: boom"] == "ERROR", "a traceback is an error, not a warning"
    assert len(api.uploads) == 1, "the screenshot of the error is kept"


def test_an_unknown_robot_is_reported_without_running_anything() -> None:
    api = FakeApi()
    _executor(api)[0].execute(job(package="not_there"))
    assert api.failed[0][1] == "robot_not_found" and api.log_batches == []
    assert api.started, "it is told to the server as a failure, after the run started"


def test_a_hostile_package_name_from_the_server_runs_nothing() -> None:
    api = FakeApi()
    _executor(api)[0].execute(job(package="../../../Windows/System32/calc"))
    assert api.failed[0][1] == "robot_not_found"


# --- cancel, timeout, stop ---------------------------------------------------------------------


def _cancel_soon(state: JobState, after: float = 1.0) -> threading.Thread:
    def go() -> None:
        time.sleep(after)
        state.cancel.set()

    thread = threading.Thread(target=go)
    thread.start()
    return thread


def test_cancelling_stops_a_robot_that_cooperates() -> None:
    api = FakeApi()
    executor, state = _executor(api, cancel_grace_seconds=5)
    thread = _cancel_soon(state)
    started = time.monotonic()
    executor.execute(job("wait"))
    thread.join()
    assert api.failed == [(job().job_id, "cancelled", "")] and api.completed == []
    assert time.monotonic() - started < 10
    assert "Cancelamento pedido. Parando o robô." in api.messages


def test_cancelling_kills_a_robot_that_ignores_requests_and_everything_it_started() -> None:
    api = FakeApi()
    executor, state = _executor(api, cancel_grace_seconds=1)
    thread = _cancel_soon(state, after=2.0)
    executor.execute(job("stubborn"))
    thread.join()
    assert api.failed[0][:2] == (job().job_id, "cancelled")
    pids = {
        key: int(m.split()[-1])
        for m in api.messages
        for key in ("child", "self")
        if m.startswith(f"{key} ")
    }
    assert set(pids) == {"child", "self"}
    time.sleep(0.5)
    for name, pid in pids.items():
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE, (
            f"{name} {pid} is still running"
        )


def test_a_cancellation_already_asked_at_start_never_starts_the_robot() -> None:
    api = FakeApi()
    api.cancel_on_start = True
    _executor(api)[0].execute(job("wait"))
    assert api.failed == [(job().job_id, "cancelled", "")]
    assert api.log_batches == []


def test_the_timeout_stops_the_robot_and_says_why() -> None:
    api = FakeApi()
    started = time.monotonic()
    _executor(api, cancel_grace_seconds=1)[0].execute(job("stubborn", timeout=2))
    assert api.failed[0][1] == "timeout"
    assert time.monotonic() - started < 15
    assert any("tempo máximo" in m for m in api.messages)


def test_stopping_the_agent_stops_the_robot_and_tells_the_server() -> None:
    api = FakeApi()
    stop = threading.Event()
    executor, _ = _executor(api, stop=stop)
    threading.Timer(1.0, stop.set).start()
    executor.execute(job("stubborn"))
    assert api.failed[0][1] == "internal"


def test_a_revoked_machine_kills_the_robot_and_propagates() -> None:
    api = FakeApi()
    api.start_error = MachineRevoked()
    executor, state = _executor(api)
    with pytest.raises(MachineRevoked):
        executor.execute(job("wait"))
    assert state.current is None


def test_the_server_forgetting_the_run_ends_it_quietly() -> None:
    api = FakeApi()
    api.start_error = JobGone("job_not_active")
    _executor(api)[0].execute(job("ok"))
    assert api.completed == [] and api.failed == []


# --- logs: cleaning and not losing --------------------------------------------------------------


def test_secrets_control_sequences_and_long_lines_are_cleaned_before_sending() -> None:
    api = FakeApi()
    _executor(api)[0].execute(job("secrets"))
    text = "\n".join(api.messages)
    for leaked in ("hunter2", "rga1.AAAA", "abc123", "rgk_zzzz", "\x1b"):
        assert leaked not in text
    assert "vermelho" in text

    api = FakeApi()
    _executor(api)[0].execute(job("noisy"))
    long = max(api.messages, key=len)
    assert len(long.encode()) <= 4096 and long.endswith("…[cortado]")


def test_batches_are_bounded_and_nothing_is_lost_or_reordered() -> None:
    api = FakeApi()
    _executor(api)[0].execute(job("noisy", lines=450))
    assert all(len(b) <= 200 for b in api.log_batches)
    numbered = [m for m in api.messages if m.startswith("linha ")]
    assert numbered == [f"linha {n}" for n in range(450)]
    seqs = [line["seq"] for line in api.lines]
    assert seqs == list(range(1, len(seqs) + 1))


def test_a_batch_the_server_could_not_take_is_kept_and_sent_again() -> None:
    api = FakeApi()
    api.log_errors = [ServerUnavailable("503"), ServerUnavailable("503")]
    _executor(api)[0].execute(job("ok"))
    assert "robo iniciado" in api.messages and "passo um" in api.messages
    assert api.completed


def test_logs_stop_when_the_server_says_the_budget_is_over() -> None:
    api = FakeApi()
    api.log_truncate_after = 3
    _executor(api)[0].execute(job("noisy", lines=400))
    # The first batch (up to 200 lines) was already on its way; nothing after the answer.
    assert len(api.lines) <= 200, "it stopped sending after the server said so"
    assert api.completed, "the run itself is not affected"


def test_logs_stop_when_the_run_is_no_longer_ours() -> None:
    api = FakeApi()
    api.log_errors = [JobGone("job_closed")]
    _executor(api)[0].execute(job("noisy", lines=50))
    assert api.completed and api.lines == []


# --- screenshots never fail a run --------------------------------------------------------------


def test_a_screenshot_that_cannot_be_uploaded_does_not_fail_the_run() -> None:
    api = FakeApi()
    api.upload_error = ServerUnavailable("storage down")
    _executor(api)[0].execute(job("ok"))
    assert api.completed and api.confirmed == []


# --- the loop ---------------------------------------------------------------------------------


def test_the_loop_runs_what_it_is_given_and_keeps_asking() -> None:
    api = FakeApi()
    stop = threading.Event()
    api.queue = [job("ok"), ServerUnavailable("down"), job("ok", package="fake_bot")]
    executor, _ = _executor(api, stop=stop)

    def stop_when_two_done() -> None:
        while len(api.completed) < 2:
            time.sleep(0.1)
        stop.set()

    helper = threading.Thread(target=stop_when_two_done)
    helper.start()
    # The retry pause after a server error is 5 s; fine for a test of the loop.
    executor.loop()
    helper.join()
    assert len(api.completed) == 2
