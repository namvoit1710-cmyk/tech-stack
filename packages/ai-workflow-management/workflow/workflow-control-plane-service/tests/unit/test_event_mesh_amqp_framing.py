"""A WebSocket frame is not an AMQP frame — feed proton within its capacity.

tenant-1, 2026-08-05, image `solace-970`:

    AMQP receiver (queue=.../sub-workflow-triggers-amqp) ended after 0.1s
    (attached=True): unable to process all bytes: 7973, 14039

Seven times, backing off 1/2/4/8/16/30s, with **zero** `AMQP FIRST delivery`
lines for that queue in the whole window — the link died on its first inbound
frame and never delivered a single message. Only `workflow.triggers` (the Jira
queue, large payloads) was affected; the small-payload queues ran clean.

The two numbers are *bytes proton consumed* and *bytes we offered* — not
"offered, processed", which is how they were first read. Raising
`max_frame_size` moved the first number 99 → 7973 and changed nothing else,
because the fault is on our side of the transport: `_pump_in` handed proton a
whole WebSocket frame while its input buffer holds `capacity()` bytes.

Proton consumes what fits and raises, so the rest of the frame is gone and the
byte stream is desynchronised. Nothing downstream can recover from that, which
is why the fix has to be at the push.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod

# The real numbers from the production failure.
CAPACITY = 7973
FRAME = 14039


class FakeTransport:
    """proton's `Transport` input semantics, including how it fails.

    `push` consumes what fits and then raises `OverflowError` with proton's
    own message — reproducing the bug is the only way to know the fix is
    aimed at it.
    """

    def __init__(self, size: int = CAPACITY, *, drains: bool = True, eos: bool = False):
        self.size = size
        self.buffered = 0
        self.received = bytearray()
        self.drains = drains
        self.eos = eos
        self.pushes: list[int] = []

    def capacity(self) -> int:
        if self.eos:
            return -1  # PN_EOS
        return self.size - self.buffered

    def push(self, data: bytes) -> None:
        room = self.capacity()
        if len(data) > room:
            # Exactly proton's binding: partial consume, then raise.
            self.received.extend(data[:room])
            self.buffered = self.size
            raise OverflowError(f"unable to process all bytes: {room}, {len(data)}")
        self.received.extend(data)
        self.buffered += len(data)
        self.pushes.append(len(data))

    def pending(self) -> int:
        return 0  # nothing to write back; keeps `_pump_out` a no-op

    def decode(self) -> None:
        """What proton does when it gets a chance to parse its buffer."""
        if self.drains:
            self.buffered = 0


class FakeCollector:
    """Draining the collector is what gives proton the chance to decode."""

    def __init__(self, transport: FakeTransport, *, on_drain=None):
        self._t = transport
        self._on_drain = on_drain

    def peek(self):
        self._t.decode()
        if self._on_drain is not None:
            self._on_drain()
        return None

    def pop(self):  # pragma: no cover - never reached, peek returns None
        pass


def _bridge(transport: FakeTransport, *, on_drain=None) -> amod._ProtonWsBridge:
    b = amod._ProtonWsBridge(
        url="wss://em.example.com/protocols/amqp10ws",
        address="queue:sub-workflow-triggers-amqp",
        queue_name="simplemdg/event/core-2/sub-workflow-triggers-amqp",
        token="tok",
        prefetch=10,
        message_handler=lambda _q, _m: True,
        is_running=lambda: True,
    )
    b._transport = transport
    b._collector = FakeCollector(transport, on_drain=on_drain)
    return b


class TestTheProductionFailure:
    def test_the_fake_reproduces_it(self):
        """Guard the guard: if this stops raising, the tests below prove nothing."""
        t = FakeTransport()
        with pytest.raises(OverflowError, match=f"unable to process all bytes: {CAPACITY}, {FRAME}"):
            t.push(b"x" * FRAME)

    def test_the_frame_that_killed_tenant_1_now_feeds_cleanly(self):
        t = FakeTransport()
        b = _bridge(t)

        assert b._feed(b"x" * FRAME) is True

        assert b.error is None
        assert b._closed is False
        assert len(t.received) == FRAME

    def test_no_byte_is_lost_or_reordered(self):
        """A desynchronised AMQP stream is unrecoverable, so this is the point."""
        data = bytes(i % 256 for i in range(FRAME))
        t = FakeTransport()
        b = _bridge(t)

        assert b._feed(data) is True
        assert bytes(t.received) == data

    def test_it_is_split_at_the_capacity_boundary(self):
        t = FakeTransport()
        b = _bridge(t)

        b._feed(b"x" * FRAME)

        assert t.pushes == [CAPACITY, FRAME - CAPACITY]


class TestOrdinaryTraffic:
    def test_a_frame_that_fits_is_still_one_push(self):
        """The small-payload queues were never broken; keep them on one push."""
        t = FakeTransport()
        b = _bridge(t)

        assert b._feed(b"x" * 512) is True
        assert t.pushes == [512]

    def test_bytes_received_is_counted_once_per_byte(self):
        t = FakeTransport()
        b = _bridge(t)

        b._feed(b"x" * FRAME)

        assert b.bytes_recv == FRAME

    def test_a_frame_spanning_many_chunks_still_arrives_whole(self):
        t = FakeTransport(size=1024)
        b = _bridge(t)
        data = bytes(i % 256 for i in range(1024 * 9 + 7))

        assert b._feed(data) is True
        assert bytes(t.received) == data
        assert len(t.pushes) == 10


class TestWhenProtonCannotAcceptMore:
    def test_a_buffer_that_never_drains_fails_the_link_rather_than_spin(self):
        """Peer ignored our max_frame_size: it cannot be assembled at all."""
        t = FakeTransport(drains=False)
        b = _bridge(t)

        assert b._feed(b"x" * FRAME) is False
        assert isinstance(b.error, RuntimeError)
        assert "capacity" in str(b.error)

    def test_that_failure_never_silently_drops_the_rest(self):
        t = FakeTransport(drains=False)
        b = _bridge(t)
        b._feed(b"x" * FRAME)

        # It reported failure instead of pretending the frame was delivered.
        assert b.error is not None
        assert len(t.received) < FRAME

    def test_end_of_stream_closes_the_bridge(self):
        t = FakeTransport(eos=True)
        b = _bridge(t)

        assert b._feed(b"x" * 16) is False
        assert b._closed is True

    def test_a_teardown_mid_frame_stops_feeding(self):
        """A handler standing the link down must not be pushed through."""
        t = FakeTransport()
        b = _bridge(t)
        b._collector = FakeCollector(t, on_drain=lambda: setattr(b, "_closed", True))

        # True: the WebSocket is fine, the transport is gone — `run` exits on
        # `_closed`, and the supervisor reconnects.
        assert b._feed(b"x" * FRAME) is True
        assert len(t.received) == CAPACITY


class TestPumpIn:
    def test_it_feeds_the_whole_websocket_frame(self):
        t = FakeTransport()
        b = _bridge(t)
        b._ws = MagicMock()
        b._ws.recv.return_value = b"x" * FRAME

        assert b._pump_in() is True
        assert len(t.received) == FRAME

    def test_a_closed_socket_still_ends_the_loop(self):
        t = FakeTransport()
        b = _bridge(t)
        b._ws = MagicMock()
        b._ws.recv.return_value = b""

        assert b._pump_in() is False

    def test_text_frames_are_encoded_before_feeding(self):
        t = FakeTransport()
        b = _bridge(t)
        b._ws = MagicMock()
        b._ws.recv.return_value = "hello"

        assert b._pump_in() is True
        assert bytes(t.received) == b"hello"

    def test_push_is_never_called_with_the_raw_frame(self):
        """The regression, stated directly: one WS frame, one push."""
        import inspect
        src = inspect.getsource(amod._ProtonWsBridge._pump_in)
        assert "_transport.push" not in src, (
            "_pump_in must delegate to _feed, which bounds each push by "
            "transport.capacity() — pushing the frame directly is the bug"
        )
