"""An in-memory pipe between an agent and a robot host, for tests that need no Windows."""

import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from regista_agent.config import AgentSettings
from regista_agent.host import HostCore, TreeContainment
from regista_agent.launcher import HostChannel, HostLauncher

_CLOSED = object()


class MemoryEnd:
    """One end of an in-memory connection: the same `send`/`receive`/`close` as the pipe."""

    def __init__(self, inbox: "queue.Queue[object]", outbox: "queue.Queue[object]") -> None:
        self._inbox = inbox
        self._outbox = outbox
        self.closed = False
        self.sent: list[bytes] = []

    def send(self, payload: bytes, *, timeout: float = 10.0) -> None:
        if self.closed:
            raise ConnectionError("fechado")
        self.sent.append(payload)
        self._outbox.put(payload)

    def receive(self, *, timeout: float = 10.0) -> bytes:
        try:
            item = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("ninguém falou") from None
        if item is _CLOSED:
            self._inbox.put(_CLOSED)  # stays closed for every later read
            raise ConnectionError("o outro lado fechou")
        assert isinstance(item, bytes)
        return item

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._outbox.put(_CLOSED)
            self._inbox.put(_CLOSED)


def memory_pair() -> tuple[MemoryEnd, MemoryEnd]:
    """(agent end, host end)."""
    a_to_h: queue.Queue[object] = queue.Queue()
    h_to_a: queue.Queue[object] = queue.Queue()
    return MemoryEnd(h_to_a, a_to_h), MemoryEnd(a_to_h, h_to_a)


class Rig:
    """An agent-side channel and launcher, and the host connecting to it."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.connections: queue.Queue[MemoryEnd] = queue.Queue()
        self.channel = HostChannel(self._accept)
        self.launcher = HostLauncher(self.channel, wait_seconds=3.0)
        self.host_threads: list[threading.Thread] = []
        self.host_ends: list[MemoryEnd] = []
        self.hosts: list[HostCore] = []

    def _accept(self) -> MemoryEnd | None:
        try:
            return self.connections.get(timeout=0.2)
        except queue.Empty:
            return None

    def connect_host(self, *, serve: bool = True) -> tuple[MemoryEnd, MemoryEnd]:
        """A host connects (a real `HostCore` serving its end, unless `serve` is False, in which
        case the caller plays the host by hand)."""
        agent_end, host_end = memory_pair()
        self.host_ends.append(host_end)
        if serve:
            core = HostCore(
                host_end,
                runs_root=self.settings.runs_dir,
                containment=TreeContainment,
                environ={},
            )
            self.hosts.append(core)
            thread = threading.Thread(target=self._serve, args=(core,), daemon=True)
            thread.start()
            self.host_threads.append(thread)
        self.connections.put(agent_end)
        return agent_end, host_end

    @staticmethod
    def _serve(core: HostCore) -> None:
        with suppress(Exception):  # a broken or violating connection ends the host
            core.serve()

    def stop(self) -> None:
        self.channel.close()
        for end in self.host_ends:
            end.close()
        for thread in self.host_threads:
            thread.join(5)


@contextmanager
def rig(settings: AgentSettings, *, connect: bool = True) -> Iterator[Rig]:
    value = Rig(settings)
    value.channel.start()
    if connect:
        value.connect_host()
    try:
        yield value
    finally:
        value.stop()


def wait_for(condition: Callable[[], bool], seconds: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


__all__ = ["MemoryEnd", "Path", "Rig", "memory_pair", "rig", "wait_for"]
