"""The named pipe between the agent and the robot host, with the real Windows calls
(docs/adr/0022). One process plays both ends; no elevation is needed.

Everything is under one `if`: the Windows calls do not exist on other systems, and the type
checker (which also runs on Linux) must not look at them there."""

import sys

if sys.platform == "win32":
    import getpass
    import os
    import struct
    import threading
    import uuid
    from collections.abc import Iterator

    import pytest

    from regista_agent import _windows, winpipe

    @pytest.fixture
    def pair() -> Iterator[tuple[winpipe.PipeConnection, winpipe.PipeConnection]]:
        """(server end, client end), connected."""
        name = f"regista-test-{uuid.uuid4().hex[:8]}"
        me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
        server = winpipe.PipeServer(name, [me])
        accepted: list[winpipe.PipeConnection] = []
        thread = threading.Thread(target=lambda: accepted.append(server.accept(timeout=10)))
        thread.start()
        client = winpipe.connect(name, timeout=10)
        thread.join(10)
        try:
            yield accepted[0], client
        finally:
            client.close()
            accepted[0].close()
            server.close()

    def test_messages_go_both_ways_whole(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        server, client = pair
        client.send(b'{"type":"hello"}')
        assert server.receive(timeout=5) == b'{"type":"hello"}'
        server.send(b"")
        assert client.receive(timeout=5) == b""
        big = os.urandom(200_000)  # bigger than the pipe's buffer: arrives in pieces
        writer = threading.Thread(target=lambda: server.send(big, timeout=10))
        writer.start()
        assert client.receive(timeout=10) == big
        writer.join(10)

    def test_a_quiet_pipe_times_out_and_a_half_frame_is_not_lost(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        server, client = pair
        with pytest.raises(TimeoutError):
            server.receive(timeout=0.2)
        frame = struct.pack("<I", 10) + b"0123456789"
        client._write_all(frame[:7], 5)  # the header and three bytes
        with pytest.raises(TimeoutError):
            server.receive(timeout=0.3)
        client._write_all(frame[7:], 5)
        assert server.receive(timeout=5) == b"0123456789", "it went on from where it was"

    def test_a_length_over_the_limit_is_refused_without_allocating_it(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        server, client = pair
        client._write_all(struct.pack("<I", 0x7FFFFFFF), 5)
        with pytest.raises(winpipe.PipeError, match="limite"):
            server.receive(timeout=5)
        with pytest.raises(winpipe.PipeError, match="grande demais"):
            client.send(b"x" * (winpipe.MAX_FRAME_BYTES + 1))

    def test_a_closed_pipe_is_an_error_not_silence(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        server, client = pair
        client.close()
        with pytest.raises(winpipe.PipeError):
            server.receive(timeout=5)

    def test_each_end_knows_who_is_on_the_other_one(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        server, client = pair
        me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
        seen = server.peer()
        assert seen.sid == me, "the SID comes from the token the pipe authenticated"
        assert seen.pid == os.getpid()
        # (In a venv made from an installed Python, `sys.executable` is a launcher and the process
        # image is the real interpreter: the kernel's answer is what counts.)
        assert seen.image is not None
        assert seen.image == winpipe.process_image(os.getpid())
        assert client.peer().pid == os.getpid()
        assert winpipe.service_process_id("NoSuchServiceAnywhere") is None
        assert winpipe.server_is_service(client, "NoSuchServiceAnywhere") is False
        assert winpipe.session_of_process(os.getpid()) is not None

    def test_only_the_named_accounts_can_open_the_pipe() -> None:
        name = f"regista-test-{uuid.uuid4().hex[:8]}"
        stranger = _windows.service_sid("SomeoneElseEntirely")
        server = winpipe.PipeServer(name, [stranger])
        try:
            with pytest.raises(winpipe.PipeError, match=r"negado|erro 5"):
                winpipe.connect(name, timeout=2)
        finally:
            server.close()

    def test_there_is_one_instance_so_nobody_else_can_squat_the_name_or_join_late(
        pair: tuple[winpipe.PipeConnection, winpipe.PipeConnection],
    ) -> None:
        me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
        server, _client = pair
        name = f"regista-test-{uuid.uuid4().hex[:8]}"
        first = winpipe.PipeServer(name, [me])
        try:
            with pytest.raises(OSError):
                winpipe.PipeServer(name, [me])  # the name is taken
            holder: list[winpipe.PipeConnection] = []
            thread = threading.Thread(target=lambda: holder.append(first.accept(timeout=10)))
            thread.start()
            one = winpipe.connect(name, timeout=5)
            thread.join(10)
            with pytest.raises(TimeoutError, match="ocupado"):
                winpipe.connect(name, timeout=0.6)  # a second client finds the instance busy
            one.close()
            holder[0].close()
        finally:
            first.close()
        assert server is not None

    def test_a_server_needs_at_least_one_valid_account() -> None:
        with pytest.raises(ValueError):
            winpipe.PipeServer("regista-test-x", [])
        with pytest.raises(ValueError):
            winpipe.PipeServer("regista-test-x", ["not-a-sid"])
