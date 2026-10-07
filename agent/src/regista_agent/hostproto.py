"""The protocol between the agent and the robot host (ADR 0022).

One message is one JSON object (the pipe frames it). The command is **one-way**: the agent says
what to run; the host starts it and reports. The host asks the agent for nothing (no token, no key,
no data from the server), and there is no message type that could carry such a request.

The host and a robot share an identity, so a compromised robot can reach the host process. That is
why the agent treats everything that comes from the host as **untrusted input**: every message is
checked against a fixed shape, a size, the one run that is on and the order the protocol allows.
Anything else is a violation: the agent drops the connection, and the worst a tampered host can do
is lie about the output and result of its own run (the accepted residual risk of the ADR).

    agent -> host:  run, cancel, ping
    host -> agent:  hello, started, output, exited, start_failed, pong

The module is pure Python (no Windows calls), so the whole protocol is tested on every system.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 262_144  # one frame; a run's environment is the biggest thing in it
MAX_LINE_BYTES = 16_384  # one line of robot output (the host cuts longer lines)
MAX_REASON_CHARS = 200
MAX_ENV_ENTRIES = 200
MAX_ENV_FIELD = 32_767  # the Windows limit of one environment variable
MAX_GRACE_SECONDS = 300
RUN_ID = re.compile(r"^[0-9a-f]{12}$")
PRIORITIES = ("below_normal", "normal")
Stream = Literal["stdout", "stderr"]


class ProtocolViolation(Exception):
    """A message the protocol does not allow (shape, size, run or order)."""


# --- what travels ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """What the agent asks the host to run. The paths are those of the run folder."""

    run_id: str
    python: str
    entry: str
    cwd: str
    env: dict[str, str]
    priority: str = "below_normal"


@dataclass(frozen=True)
class Hello:
    protocol: int


@dataclass(frozen=True)
class Started:
    run_id: str
    pid: int


@dataclass(frozen=True)
class Output:
    run_id: str
    stream: Stream
    line: str


@dataclass(frozen=True)
class Exited:
    run_id: str
    code: int


@dataclass(frozen=True)
class StartFailed:
    run_id: str
    reason: str


@dataclass(frozen=True)
class Pong:
    pass


HostMessage = Hello | Started | Output | Exited | StartFailed | Pong


@dataclass(frozen=True)
class Run:
    spec: RunSpec


@dataclass(frozen=True)
class Cancel:
    run_id: str
    grace_seconds: int


@dataclass(frozen=True)
class Ping:
    pass


AgentMessage = Run | Cancel | Ping


# --- encoding (never fails for a well-formed value) ---------------------------------------------


def _frame(body: dict[str, Any]) -> bytes:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolViolation("mensagem grande demais")
    return raw


def encode_agent(message: AgentMessage) -> bytes:
    if isinstance(message, Run):
        spec = message.spec
        return _frame(
            {
                "type": "run",
                "run_id": spec.run_id,
                "python": spec.python,
                "entry": spec.entry,
                "cwd": spec.cwd,
                "env": spec.env,
                "priority": spec.priority,
            }
        )
    if isinstance(message, Cancel):
        return _frame(
            {"type": "cancel", "run_id": message.run_id, "grace_seconds": message.grace_seconds}
        )
    return _frame({"type": "ping"})


def encode_host(message: HostMessage) -> bytes:
    if isinstance(message, Hello):
        return _frame({"type": "hello", "protocol": message.protocol})
    if isinstance(message, Started):
        return _frame({"type": "started", "run_id": message.run_id, "pid": message.pid})
    if isinstance(message, Output):
        return _frame(
            {
                "type": "output",
                "run_id": message.run_id,
                "stream": message.stream,
                "line": message.line,
            }
        )
    if isinstance(message, Exited):
        return _frame({"type": "exited", "run_id": message.run_id, "code": message.code})
    if isinstance(message, StartFailed):
        return _frame({"type": "start_failed", "run_id": message.run_id, "reason": message.reason})
    return _frame({"type": "pong"})


# --- strict decoding ---------------------------------------------------------------------------


def _object(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolViolation("mensagem grande demais")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolViolation("a mensagem não é JSON") from None
    if not isinstance(data, dict):
        raise ProtocolViolation("a mensagem não é um objeto")
    return data


def _exact(data: dict[str, Any], keys: set[str]) -> None:
    """The message has exactly these fields: nothing missing, nothing extra."""
    if set(data) != keys:
        raise ProtocolViolation(f"campos inesperados em '{data.get('type')}'")


def _int(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProtocolViolation(f"{what} inválido")
    return value


def _text(value: Any, max_bytes: int, what: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8", "replace")) > max_bytes:
        raise ProtocolViolation(f"{what} inválido ou grande demais")
    return value


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ProtocolViolation("identificador da execução inválido")
    return value


def decode_host(raw: bytes) -> HostMessage:
    """One message from the host, checked for shape and size (not yet for order or run)."""
    data = _object(raw)
    kind = data.get("type")
    if kind == "hello":
        _exact(data, {"type", "protocol"})
        return Hello(_int(data["protocol"], 0, 1_000, "protocolo"))
    if kind == "started":
        _exact(data, {"type", "run_id", "pid"})
        return Started(_run_id(data["run_id"]), _int(data["pid"], 1, 2**31 - 1, "pid"))
    if kind == "output":
        _exact(data, {"type", "run_id", "stream", "line"})
        if data["stream"] not in ("stdout", "stderr"):
            raise ProtocolViolation("canal de saída desconhecido")
        return Output(
            _run_id(data["run_id"]), data["stream"], _text(data["line"], MAX_LINE_BYTES, "linha")
        )
    if kind == "exited":
        _exact(data, {"type", "run_id", "code"})
        return Exited(_run_id(data["run_id"]), _int(data["code"], -(2**31), 2**32, "código"))
    if kind == "start_failed":
        _exact(data, {"type", "run_id", "reason"})
        reason = _text(data["reason"], MAX_REASON_CHARS * 4, "motivo")
        if len(reason) > MAX_REASON_CHARS:
            raise ProtocolViolation("motivo grande demais")
        return StartFailed(_run_id(data["run_id"]), reason)
    if kind == "pong":
        _exact(data, {"type"})
        return Pong()
    raise ProtocolViolation("tipo de mensagem desconhecido")


def decode_agent(raw: bytes) -> AgentMessage:
    """One message from the agent, as the host reads it."""
    data = _object(raw)
    kind = data.get("type")
    if kind == "run":
        _exact(data, {"type", "run_id", "python", "entry", "cwd", "env", "priority"})
        env = data["env"]
        if (
            not isinstance(env, dict)
            or len(env) > MAX_ENV_ENTRIES
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            raise ProtocolViolation("ambiente inválido")
        for key, value in env.items():
            if not key or "=" in key or "\0" in key or len(key) > 256 or "\0" in value:
                raise ProtocolViolation("variável de ambiente inválida")
            if len(value) > MAX_ENV_FIELD:
                raise ProtocolViolation("variável de ambiente grande demais")
        if data["priority"] not in PRIORITIES:
            raise ProtocolViolation("prioridade desconhecida")
        return Run(
            RunSpec(
                _run_id(data["run_id"]),
                _text(data["python"], 4096, "python"),
                _text(data["entry"], 4096, "entry"),
                _text(data["cwd"], 4096, "cwd"),
                dict(env),
                data["priority"],
            )
        )
    if kind == "cancel":
        _exact(data, {"type", "run_id", "grace_seconds"})
        return Cancel(
            _run_id(data["run_id"]), _int(data["grace_seconds"], 0, MAX_GRACE_SECONDS, "prazo")
        )
    if kind == "ping":
        _exact(data, {"type"})
        return Ping()
    raise ProtocolViolation("tipo de mensagem desconhecido")


# --- order: what the agent accepts, and when ---------------------------------------------------


@dataclass
class HostConversation:
    """The agent's side of one connection: which message may come next.

    `hello` first, once. Then, per run: the agent sends `run` (`begin_run`), the host answers
    `started` (or `start_failed`, which ends the run), then any number of `output`, then `exited`.
    `pong` is accepted only against a `ping` the agent sent. Everything carries the id of the one
    run that is on; a message of any other run is a violation, not something to ignore."""

    _state: str = "await_hello"
    _run_id: str | None = None
    _pings: int = 0
    _ended: bool = field(default=False)

    def begin_run(self, run_id: str) -> None:
        if self._state != "idle":
            raise ProtocolViolation("já há uma execução em andamento nesta conexão")
        self._state, self._run_id = "run_sent", run_id

    def ping_sent(self) -> None:
        self._pings += 1

    @property
    def idle(self) -> bool:
        return self._state == "idle"

    def accept(self, raw: bytes) -> HostMessage:
        """Decode and order-check one message from the host."""
        message = decode_host(raw)
        if isinstance(message, Hello):
            if self._state != "await_hello":
                raise ProtocolViolation("hello fora de hora")
            if message.protocol != PROTOCOL_VERSION:
                raise ProtocolViolation(f"versão de protocolo {message.protocol} não suportada")
            self._state = "idle"
            return message
        if isinstance(message, Pong):
            if self._pings <= 0:
                raise ProtocolViolation("pong sem ping")
            self._pings -= 1
            return message
        if self._state in ("await_hello", "idle") or self._run_id is None:
            raise ProtocolViolation(f"mensagem {type(message).__name__} sem execução em andamento")
        if message.run_id != self._run_id:
            raise ProtocolViolation("mensagem de outra execução")
        if isinstance(message, Started):
            if self._state != "run_sent":
                raise ProtocolViolation("started fora de ordem")
            self._state = "running"
        elif isinstance(message, StartFailed):
            if self._state != "run_sent":
                raise ProtocolViolation("start_failed fora de ordem")
            self._state, self._run_id = "idle", None
        elif isinstance(message, Output):
            if self._state != "running":
                raise ProtocolViolation("saída fora de ordem")
        elif isinstance(message, Exited):
            if self._state != "running":
                raise ProtocolViolation("exited fora de ordem")
            self._state, self._run_id = "idle", None
        return message


def within(path: str, root: Path) -> bool:
    """True when `path` (resolved, links followed) is inside `root`. The host uses it so that even
    a message from the agent cannot make it run something outside the run folder."""
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False
