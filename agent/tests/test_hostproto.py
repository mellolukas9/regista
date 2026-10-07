"""The protocol between the agent and the robot host, from the agent's side (docs/adr/0022).

The host runs as the robot's identity, so a compromised robot can speak through it. Everything
that comes from the host is untrusted input: these tests are the table of what is refused."""

import json
from typing import Any

import pytest

from regista_agent import hostproto
from regista_agent.hostproto import (
    Exited,
    Hello,
    HostConversation,
    Output,
    Pong,
    ProtocolViolation,
    Started,
    StartFailed,
)

RUN = "0123456789ab"
OTHER = "ba9876543210"


def raw(**fields: Any) -> bytes:
    return json.dumps(fields).encode()


def greeted() -> HostConversation:
    conversation = HostConversation()
    conversation.accept(raw(type="hello", protocol=hostproto.PROTOCOL_VERSION))
    return conversation


def running() -> HostConversation:
    conversation = greeted()
    conversation.begin_run(RUN)
    conversation.accept(raw(type="started", run_id=RUN, pid=4242))
    return conversation


# --- what a good host says ---------------------------------------------------------------------


def test_a_run_goes_hello_started_output_exited_and_the_host_is_free_again() -> None:
    conversation = greeted()
    assert conversation.idle
    conversation.begin_run(RUN)
    assert conversation.accept(raw(type="started", run_id=RUN, pid=7)) == Started(RUN, 7)
    out = conversation.accept(raw(type="output", run_id=RUN, stream="stderr", line="olá"))
    assert out == Output(RUN, "stderr", "olá")
    assert conversation.accept(raw(type="exited", run_id=RUN, code=0)) == Exited(RUN, 0)
    assert conversation.idle, "and it can take another run on the same connection"


def test_a_start_that_fails_ends_the_run() -> None:
    conversation = greeted()
    conversation.begin_run(RUN)
    message = conversation.accept(raw(type="start_failed", run_id=RUN, reason="sem Python"))
    assert message == StartFailed(RUN, "sem Python") and conversation.idle


def test_a_pong_is_accepted_against_a_ping_the_agent_sent() -> None:
    conversation = greeted()
    conversation.ping_sent()
    assert conversation.accept(raw(type="pong")) == Pong()


def test_the_hello_carries_the_protocol_version() -> None:
    assert HostConversation().accept(raw(type="hello", protocol=1)) == Hello(1)


# --- everything a tampered host might send, refused --------------------------------------------

BAD_SHAPES = {
    "not json": b"\xff\xfe nope",
    "a list, not an object": b"[1, 2]",
    "a string": b'"started"',
    "no type": raw(run_id=RUN),
    "unknown type": raw(type="shell", run_id=RUN),
    "a request for the token": raw(type="get_token"),
    "a request for the key": raw(type="read_file", path="C:/ProgramData/Regista/keys/machine.key"),
    "a request for the server": raw(type="http", url="https://api.example"),
    "started with an extra field": raw(type="started", run_id=RUN, pid=1, path="x"),
    "started without pid": raw(type="started", run_id=RUN),
    "pid as text": raw(type="started", run_id=RUN, pid="1"),
    "pid zero": raw(type="started", run_id=RUN, pid=0),
    "pid as a boolean": raw(type="started", run_id=RUN, pid=True),
    "a run id with a path in it": raw(type="started", run_id="../../keys", pid=1),
    "a run id too long": raw(type="started", run_id="0" * 40, pid=1),
}


@pytest.mark.parametrize("payload", list(BAD_SHAPES.values()), ids=list(BAD_SHAPES))
def test_a_malformed_message_is_refused(payload: bytes) -> None:
    conversation = greeted()
    conversation.begin_run(RUN)
    with pytest.raises(ProtocolViolation):
        conversation.accept(payload)


def test_a_message_of_another_run_is_refused() -> None:
    conversation = running()
    with pytest.raises(ProtocolViolation, match="outra execução"):
        conversation.accept(raw(type="output", run_id=OTHER, stream="stdout", line="x"))
    with pytest.raises(ProtocolViolation, match="outra execução"):
        running().accept(raw(type="exited", run_id=OTHER, code=0))


def test_a_message_when_no_run_is_on_is_refused() -> None:
    for payload in (
        raw(type="output", run_id=RUN, stream="stdout", line="x"),
        raw(type="exited", run_id=RUN, code=0),
        raw(type="started", run_id=RUN, pid=1),
    ):
        with pytest.raises(ProtocolViolation):
            greeted().accept(payload)


@pytest.mark.parametrize(
    "payload",
    [
        raw(type="output", run_id=RUN, stream="stdout", line="before started"),
        raw(type="exited", run_id=RUN, code=0),
    ],
)
def test_output_or_exit_before_the_start_is_out_of_order(payload: bytes) -> None:
    conversation = greeted()
    conversation.begin_run(RUN)
    with pytest.raises(ProtocolViolation, match="fora de ordem"):
        conversation.accept(payload)


def test_started_twice_and_anything_after_exited_are_out_of_order() -> None:
    with pytest.raises(ProtocolViolation, match="fora de ordem"):
        running().accept(raw(type="started", run_id=RUN, pid=9))
    done = running()
    done.accept(raw(type="exited", run_id=RUN, code=0))
    with pytest.raises(ProtocolViolation):
        done.accept(raw(type="output", run_id=RUN, stream="stdout", line="late"))


def test_a_second_hello_and_a_first_message_that_is_not_hello_are_refused() -> None:
    with pytest.raises(ProtocolViolation, match="fora de hora"):
        greeted().accept(raw(type="hello", protocol=1))
    with pytest.raises(ProtocolViolation):
        HostConversation().accept(raw(type="pong"))
    with pytest.raises(ProtocolViolation):
        HostConversation().accept(raw(type="started", run_id=RUN, pid=1))


def test_a_protocol_version_the_agent_does_not_speak_is_refused() -> None:
    with pytest.raises(ProtocolViolation, match="versão"):
        HostConversation().accept(raw(type="hello", protocol=99))


def test_a_pong_nobody_asked_for_is_refused() -> None:
    with pytest.raises(ProtocolViolation, match="pong sem ping"):
        greeted().accept(raw(type="pong"))


def test_output_stream_must_be_stdout_or_stderr() -> None:
    with pytest.raises(ProtocolViolation):
        running().accept(raw(type="output", run_id=RUN, stream="stdin", line="x"))


def test_a_line_over_the_limit_and_a_frame_over_the_limit_are_refused() -> None:
    big_line = "x" * (hostproto.MAX_LINE_BYTES + 1)
    with pytest.raises(ProtocolViolation):
        running().accept(raw(type="output", run_id=RUN, stream="stdout", line=big_line))
    with pytest.raises(ProtocolViolation, match="grande demais"):
        running().accept(b" " * (hostproto.MAX_MESSAGE_BYTES + 1))
    exactly = "é" * (hostproto.MAX_LINE_BYTES // 2)  # counted in bytes, not characters
    running().accept(
        json.dumps(
            {"type": "output", "run_id": RUN, "stream": "stdout", "line": exactly},
            ensure_ascii=False,
        ).encode()
    )
    with pytest.raises(ProtocolViolation):
        running().accept(
            json.dumps(
                {"type": "output", "run_id": RUN, "stream": "stdout", "line": exactly + "é"},
                ensure_ascii=False,
            ).encode()
        )


def test_a_reason_over_the_limit_is_refused() -> None:
    conversation = greeted()
    conversation.begin_run(RUN)
    with pytest.raises(ProtocolViolation):
        conversation.accept(raw(type="start_failed", run_id=RUN, reason="x" * 500))


@pytest.mark.parametrize("code", ["0", None, 1.5, 2**40, -(2**40)])
def test_an_exit_code_must_be_an_integer_in_range(code: Any) -> None:
    with pytest.raises(ProtocolViolation):
        running().accept(raw(type="exited", run_id=RUN, code=code))


def test_two_runs_cannot_be_on_one_connection() -> None:
    conversation = greeted()
    conversation.begin_run(RUN)
    with pytest.raises(ProtocolViolation):
        conversation.begin_run(OTHER)


# --- what the host reads from the agent --------------------------------------------------------


def good_run(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "type": "run",
        "run_id": RUN,
        "python": "p",
        "entry": "e",
        "cwd": "c",
        "env": {"A": "1"},
        "priority": "normal",
    }
    fields.update(over)
    return fields


def test_the_host_reads_a_run_exactly() -> None:
    message = hostproto.decode_agent(json.dumps(good_run()).encode())
    assert isinstance(message, hostproto.Run) and message.spec.env == {"A": "1"}
    roundtrip = hostproto.decode_agent(hostproto.encode_agent(message))
    assert roundtrip == message


@pytest.mark.parametrize(
    "fields",
    [
        {"env": {"A": 1}},
        {"env": {"A=B": "x"}},
        {"env": {"": "x"}},
        {"env": {f"K{n}": "v" for n in range(300)}},
        {"env": {"A": "x" * 40000}},
        {"priority": "realtime"},
        {"run_id": "../x"},
        {"extra": 1},
    ],
)
def test_the_host_refuses_a_malformed_run(fields: dict[str, Any]) -> None:
    with pytest.raises(ProtocolViolation):
        hostproto.decode_agent(json.dumps(good_run(**fields)).encode())


def test_the_host_reads_cancel_and_ping_and_nothing_else() -> None:
    assert hostproto.decode_agent(raw(type="cancel", run_id=RUN, grace_seconds=5)) == (
        hostproto.Cancel(RUN, 5)
    )
    assert hostproto.decode_agent(raw(type="ping")) == hostproto.Ping()
    for payload in (
        raw(type="cancel", run_id=RUN, grace_seconds=9999),
        raw(type="cancel", run_id=RUN, grace_seconds=-1),
        raw(type="exec", cmd="calc.exe"),
    ):
        with pytest.raises(ProtocolViolation):
            hostproto.decode_agent(payload)
