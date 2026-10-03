"""Sending a robot's output to the server in batches (docs/specs/orchestration.md).

A robot's output is untrusted data and may carry secrets by accident, so every line is cleaned
before it leaves the machine: secrets scrubbed (the same net as the agent's own log), terminal
control sequences removed, long lines cut. The server cleans again; this is the first net.

Lines are numbered on the machine (`seq`), so a batch sent twice is stored once. A batch the server
could not take (network trouble, a missing partition, a 5xx) is kept and sent again, never dropped
in silence; only a run that is over budget or no longer ours stops the sending.
"""

import logging
import re
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from regista_agent.errors import MachineRevoked, ServerUnavailable
from regista_agent.jobapi import JobApi, JobGone
from regista_agent.logs import scrub

log = logging.getLogger("regista_agent")

MAX_LINE_BYTES = 4096
MAX_BATCH_LINES = 200
# Under the 256 KB the server accepts, with room for the JSON around the text.
MAX_BATCH_BYTES = 96 * 1024
MAX_PENDING_LINES = 10_000

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_LEVEL = re.compile(r"^\s*(?:\[?)(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)(?:\]?)\b[:\s\-|]*")
_TRACEBACK = re.compile(r"^(Traceback \(most recent call last\)|\s+File \"|\w*(Error|Exception)\b)")
_LEVELS = {
    "DEBUG": "INFO",
    "INFO": "INFO",
    "WARNING": "WARN",
    "WARN": "WARN",
    "ERROR": "ERROR",
    "CRITICAL": "ERROR",
    "FATAL": "ERROR",
}


def clean(message: str) -> str:
    text = _CONTROL.sub("", _ANSI.sub("", scrub(message)))
    raw = text.encode("utf-8")
    if len(raw) <= MAX_LINE_BYTES:
        return text
    marker = "…[cortado]"
    keep = MAX_LINE_BYTES - len(marker.encode("utf-8"))
    return raw[:keep].decode("utf-8", errors="ignore") + marker


def classify(message: str, *, stream: str) -> tuple[str, str]:
    """`(level, text)`. A line that starts with a level name keeps it (and loses the prefix);
    a traceback is an error; otherwise stdout is INFO and stderr is WARN."""
    match = _LEVEL.match(message)
    if match:
        return _LEVELS[match.group(1)], message[match.end() :]
    if _TRACEBACK.match(message):
        return "ERROR", message
    return ("WARN" if stream == "stderr" else "INFO"), message


class LogShipper:
    def __init__(self, api: JobApi, job_id: str, *, flush_seconds: float = 2.0) -> None:
        self._api = api
        self._job_id = job_id
        self._flush_seconds = flush_seconds
        self._lock = threading.Lock()
        self._pending: deque[dict[str, Any]] = deque()
        self._seq = 0
        self._dropped = 0
        self._stopped = False  # over budget, or the run is no longer ours
        self._wake = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="log-shipper", daemon=True)
        self.tail: deque[str] = deque(maxlen=20)  # last lines of stderr, for a failure message

    def start(self) -> None:
        self._thread.start()

    def add(self, stream: str, message: str) -> None:
        """Called from the threads that read the robot's output."""
        level, text = classify(message, stream=stream)
        text = clean(text)
        if not text.strip():
            return
        if stream == "stderr":
            self.tail.append(text)
        with self._lock:
            if self._stopped:
                return
            self._seq += 1
            self._pending.append(
                {
                    "seq": self._seq,
                    "ts": datetime.now(UTC).isoformat(),
                    "level": level,
                    "message": text,
                }
            )
            if len(self._pending) > MAX_PENDING_LINES:
                self._pending.popleft()
                self._dropped += 1
            full = len(self._pending) >= MAX_BATCH_LINES
        if full:
            self._wake.set()

    def note(self, level: str, message: str) -> None:
        """A line of the agent itself (not the robot's), e.g. "Cancelamento pedido"."""
        self.add("agent_" + level, f"{level} {message}")

    def _take_batch(self) -> list[dict[str, Any]]:
        with self._lock:
            batch: list[dict[str, Any]] = []
            size = 0
            while self._pending and len(batch) < MAX_BATCH_LINES:
                line = self._pending[0]
                weight = len(line["message"].encode("utf-8")) + 80
                if batch and size + weight > MAX_BATCH_BYTES:
                    break
                batch.append(self._pending.popleft())
                size += weight
            return batch

    def _give_back(self, batch: list[dict[str, Any]]) -> None:
        with self._lock:
            self._pending.extendleft(reversed(batch))

    def _send_once(self) -> bool:
        """Send one batch. True when something was sent (so there may be more)."""
        batch = self._take_batch()
        if not batch:
            return False
        try:
            result = self._api.send_logs(self._job_id, batch)
        except JobGone as exc:
            log.warning("logs of %s are no longer wanted: %s", self._job_id, exc)
            with self._lock:
                self._stopped = True
                self._pending.clear()
            return False
        except MachineRevoked:
            # The run's own thread and the heartbeat see the revocation and stop everything; this
            # thread only has to stop quietly instead of dying with a traceback.
            log.warning("logs of %s not sent: the machine was revoked", self._job_id)
            with self._lock:
                self._stopped = True
                self._pending.clear()
            return False
        except ServerUnavailable as exc:
            log.warning("logs not sent, will try again: %s", exc)
            self._give_back(batch)
            return False
        if result.truncated:
            log.warning("the log budget of run %s is used up", self._job_id)
            with self._lock:
                self._stopped = True
                self._pending.clear()
            return False
        return True

    def _run(self) -> None:
        while not self._done.is_set():
            self._wake.wait(self._flush_seconds)
            self._wake.clear()
            while self._send_once():
                pass

    def close(self, *, patience_seconds: float = 30.0) -> None:
        """Send whatever is left, retrying for a while, then stop."""
        self._done.set()
        self._wake.set()
        self._thread.join(timeout=patience_seconds)
        deadline = patience_seconds
        waited = 0.0
        while True:
            while self._send_once():
                pass
            with self._lock:
                left = len(self._pending)
            if left == 0 or self._stopped or waited >= deadline:
                break
            threading.Event().wait(1.0)
            waited += 1.0
        if left:
            log.error("%s log lines of run %s could not be sent", left, self._job_id)
        if self._dropped:
            log.error(
                "%s log lines of run %s were dropped (too many pending)",
                self._dropped,
                self._job_id,
            )
