"""G2 — detect a link that is attached but silent, and rotate before the token dies.

The failure this exists for, from tenant-1 on 2026-08-05: six receiver links
opened cleanly and then delivered nothing. No errors, no detaches, no
disconnects. `_receive_loop` only reconnects when `run()` returns, and a silent
link never returns — so the loop spun forever consuming nothing while every run
hung with all nodes NOT_STARTED. Nothing in the system could see it.

A stall needs BOTH halves: no deliveries since the last check AND the broker
says messages are waiting. Depth alone isn't a stall (the handler may be slow);
a quiet queue isn't a stall either (that's just an idle system).

Second half: the SASL token is valid 12 hours (measured), and the bridge held
one token for the life of the connection because `_token()` threw the expiry
away. A healthy link therefore crossed expiry roughly once a day.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod

QUEUE = "simplemdg/event/core-2/sub-orchestration-actions-amqp"


def _consumer(depth_fn) -> amod.EventMeshAMQPConsumer:
    return amod.EventMeshAMQPConsumer(
        host="em.example.com",
        get_token=lambda: ("tok", time.time() + 43_200),
        message_handler=lambda _q, _b: True,
        queue_depth=depth_fn,
    )


def _bridge(*, ready=True, deliveries=0, epoch=1) -> MagicMock:
    b = MagicMock()
    b.ready = ready
    b.deliveries = deliveries
    b.epoch = epoch
    b.request_stop = MagicMock()
    return b


class TestStallDetection:
    def test_a_silent_link_with_messages_waiting_is_stood_down(self):
        c = _consumer(lambda _q: 7)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        t = 1000.0

        assert c.check_liveness(now=t) == []           # first sight: arm the clock
        assert c.check_liveness(now=t + 10) == []      # not long enough yet
        assert c.check_liveness(now=t + amod._STALL_AFTER_S + 1) == [QUEUE]

        b.request_stop.assert_called_once_with("watchdog_stall")
        assert c.forced_reconnects == 1

    def test_a_delivering_link_is_never_stood_down(self):
        deliveries = {"n": 0}
        c = _consumer(lambda _q: 500)  # deep queue, but it IS draining
        b = _bridge()
        c._bridges[(QUEUE, 0)] = b
        for i in range(10):
            deliveries["n"] += 3
            b.deliveries = deliveries["n"]
            assert c.check_liveness(now=1000.0 + i * 60) == []
        b.request_stop.assert_not_called()

    def test_an_empty_queue_is_not_a_stall(self):
        """An idle system delivers nothing. That is not a fault."""
        c = _consumer(lambda _q: 0)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        for i in range(10):
            assert c.check_liveness(now=1000.0 + i * 600) == []
        b.request_stop.assert_not_called()

    def test_an_unreadable_depth_is_not_a_stall(self):
        c = _consumer(lambda _q: None)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        for i in range(10):
            assert c.check_liveness(now=1000.0 + i * 600) == []
        b.request_stop.assert_not_called()

    def test_a_depth_lookup_that_raises_does_not_kill_the_watchdog(self):
        def boom(_q):
            raise RuntimeError("management API down")

        c = _consumer(boom)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        assert c.check_liveness(now=1000.0) == []
        assert c.check_liveness(now=2000.0) == []
        b.request_stop.assert_not_called()

    def test_a_link_that_is_not_ready_is_left_to_the_reconnect_loop(self):
        c = _consumer(lambda _q: 7)
        b = _bridge(ready=False, deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        assert c.check_liveness(now=1000.0) == []
        assert c.check_liveness(now=1000.0 + amod._STALL_AFTER_S + 1) == []
        b.request_stop.assert_not_called()

    def test_delivery_after_arming_clears_the_stall_clock(self):
        c = _consumer(lambda _q: 7)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        c.check_liveness(now=1000.0)               # armed
        b.deliveries = 1                            # one message got through
        c.check_liveness(now=1000.0 + 30)          # clears
        assert c.check_liveness(now=1000.0 + amod._STALL_AFTER_S + 1) == []
        b.request_stop.assert_not_called()

    def test_repeated_forced_reconnects_widen_the_window(self):
        """A queue that genuinely cannot deliver must not reconnect-storm."""
        c = _consumer(lambda _q: 7)
        b = _bridge(deliveries=0)
        c._bridges[(QUEUE, 0)] = b
        t = 1000.0
        c.check_liveness(now=t)
        assert c.check_liveness(now=t + amod._STALL_AFTER_S + 1) == [QUEUE]

        # Second round now needs 2x the window.
        t += amod._STALL_AFTER_S + 2
        c.check_liveness(now=t)
        assert c.check_liveness(now=t + amod._STALL_AFTER_S + 1) == []
        assert c.check_liveness(now=t + 2 * amod._STALL_AFTER_S + 1) == [QUEUE]
        assert c.forced_reconnects == 2

    def test_without_a_depth_source_the_watchdog_is_inert(self):
        c = amod.EventMeshAMQPConsumer(
            host="em.example.com",
            get_token=lambda: ("tok", 0.0),
            message_handler=lambda _q, _b: True,
        )
        c._bridges[(QUEUE, 0)] = _bridge(deliveries=0)
        assert c.check_liveness(now=1000.0) == []


class TestTokenRotation:
    def test_the_expiry_is_carried_to_the_bridge(self):
        c = _consumer(lambda _q: 0)
        token, expires_at = c._token_with_expiry()
        assert token == "tok"
        assert expires_at > time.time() + 43_000

    @pytest.mark.parametrize("bad", [None, "", "not-a-number"])
    def test_an_unusable_expiry_degrades_to_never_rotate(self, bad):
        c = amod.EventMeshAMQPConsumer(
            host="em.example.com",
            get_token=lambda: ("tok", bad),
            message_handler=lambda _q, _b: True,
        )
        assert c._token_with_expiry() == ("tok", 0.0)


class TestBridgeStandDown:
    def _make(self, **kw):
        return amod._ProtonWsBridge(
            url="wss://em.example.com/protocols/amqp10ws",
            address=f"queue:{QUEUE}",
            queue_name=QUEUE,
            token="tok",
            prefetch=1,
            message_handler=lambda _q, _b: True,
            is_running=lambda: True,
            **kw,
        )

    def test_a_token_inside_the_margin_triggers_rotation(self):
        b = self._make(expires_at=time.time() + 10)
        assert b._token_expiring() is True

    def test_a_fresh_token_does_not(self):
        b = self._make(expires_at=time.time() + 43_200)
        assert b._token_expiring() is False

    def test_an_unknown_expiry_never_rotates(self):
        """Better to run on an old token than to reconnect-loop on a guess."""
        b = self._make(expires_at=0.0)
        assert b._token_expiring() is False

    def test_request_stop_marks_the_reason_and_closes(self):
        b = self._make()
        b.request_stop("watchdog_stall")
        assert b.stood_down == "watchdog_stall"
        assert b._closed is True


class TestMaxFrameSize:
    """Bound what the *peer* may send us in one frame.

    Measured on tenant-1 2026-08-05: 31 reconnects in ~7 minutes on exactly the
    three large-payload queues while orchestration.actions (small messages)
    never flapped once. The watchdog could not see it — the link was attached
    AND delivering, right until it died — which is why the reconnect rate is
    now its own alert.

    This is only half the protection and it did **not** fix the flapping on its
    own: it moved proton's input capacity from 99 to 7973 bytes, and a
    14039-byte frame still killed the link. The other half — feeding proton
    within that capacity — is `test_event_mesh_amqp_framing.py`. A frame larger
    than the buffer can never be assembled, so both are needed.
    """

    def test_the_default_covers_the_publisher_cap(self):
        from app.layer4_frameworks.messaging.event_mesh_config import (
            _DEFAULT_MAX_PAYLOAD_BYTES,
        )
        assert amod._MAX_FRAME_BYTES > _DEFAULT_MAX_PAYLOAD_BYTES, (
            "a frame smaller than the largest payload we can publish means the "
            "link dies on our own messages"
        )

    def test_the_floor_is_not_below_the_observed_failure(self):
        # The message that killed the link was 7973 bytes; the minimum must be
        # far above it so a bad env value cannot reintroduce the bug.
        assert amod._MAX_FRAME_BYTES >= 65_536

    def test_it_is_applied_to_the_transport_before_bind(self):
        """After bind the value is already negotiated — setting it later is a
        silent no-op, which would look exactly like the bug."""
        import inspect
        src = inspect.getsource(amod._ProtonWsBridge._open)
        set_at = src.index("max_frame_size")
        bind_at = src.index(".bind(")
        assert set_at < bind_at, "max_frame_size must be set BEFORE transport.bind()"


class TestAReconnectDoesNotLookLikeSilence:
    """`deliveries` restarts at 0 on every new bridge, but the watchdog's
    baseline lived per QUEUE and survived the swap. So after any reconnect it
    compared a counter starting at 0 against the previous link's lifetime
    total, read "no progress", and — if the queue had work waiting — stood down
    a link that was delivering perfectly well. Its replacement then started
    from 0 again, and so on.

    A self-perpetuating false stall that also flatlines the delivery counter:
    the exact symptom set this watchdog exists to detect, manufactured by the
    watchdog. No concurrency needed — the 12-hour token rotation above is
    enough to trigger it.
    """

    def test_a_fresh_bridge_gets_a_fresh_baseline(self):
        c = _consumer(lambda _q: 0)
        c._metrics = MagicMock()
        c._bridges[(QUEUE, 0)] = _bridge(deliveries=5000, epoch=1)
        c.check_liveness(now=1000.0)

        c._bridges[(QUEUE, 0)] = _bridge(deliveries=2, epoch=2)   # reconnect
        c._metrics = MagicMock()
        c.check_liveness(now=1001.0)

        c._metrics.record_amqp_delivery.assert_called_once_with(QUEUE, 2)

    def test_a_reconnect_on_a_busy_queue_is_not_stood_down(self):
        c = _consumer(lambda _q: 500)
        c._bridges[(QUEUE, 0)] = _bridge(deliveries=5000, epoch=1)
        c.check_liveness(now=0.0)

        new = _bridge(deliveries=3, epoch=2)
        c._bridges[(QUEUE, 0)] = new
        for t in (1.0, amod._STALL_AFTER_S + 2, amod._STALL_AFTER_S * 4 + 3):
            c.check_liveness(now=t)
            new.deliveries += 3

        new.request_stop.assert_not_called()
        assert c.forced_reconnects == 0

    def test_a_genuinely_silent_new_bridge_is_still_stood_down(self):
        """The reset must not blind the watchdog."""
        c = _consumer(lambda _q: 7)
        c._bridges[(QUEUE, 0)] = _bridge(deliveries=5000, epoch=1)
        c.check_liveness(now=0.0)

        silent = _bridge(deliveries=0, epoch=2)
        c._bridges[(QUEUE, 0)] = silent
        c.check_liveness(now=1.0)
        c.check_liveness(now=amod._STALL_AFTER_S + 2)

        silent.request_stop.assert_called_once_with("watchdog_stall")

    def test_the_forced_streak_survives_the_reset(self):
        """Otherwise every stand-down makes a new bridge, the streak resets,
        and the window never widens — a dead queue reconnect-storms forever."""
        c = _consumer(lambda _q: 7)
        c._bridges[(QUEUE, 0)] = _bridge(deliveries=0, epoch=1)
        c.check_liveness(now=0.0)
        c.check_liveness(now=amod._STALL_AFTER_S + 1)
        assert c.forced_reconnects == 1

        c._bridges[(QUEUE, 0)] = _bridge(deliveries=0, epoch=2)
        c.check_liveness(now=amod._STALL_AFTER_S + 2)
        c.check_liveness(now=amod._STALL_AFTER_S * 2 + 3)   # one window: too soon
        assert c.forced_reconnects == 1
        c.check_liveness(now=amod._STALL_AFTER_S * 3 + 4)   # widened window
        assert c.forced_reconnects == 2


class TestEveryLinkIsWatched:
    """`concurrency` opens that many links onto the SAME queue. Keyed by queue
    they overwrote each other in `_bridges`, so only the last to register was
    watched — the other N-1 could go silent unseen, and `links_attached`
    reported 1 while 4 existed."""

    def _four(self, depth):
        c = _consumer(depth)
        bridges = [_bridge(deliveries=0, epoch=i + 1) for i in range(4)]
        for i, b in enumerate(bridges):
            c._bridges[(QUEUE, i)] = b
        return c, bridges

    def test_all_four_are_registered_not_collapsed_to_one(self):
        c, _ = self._four(lambda _q: 0)
        assert len(c._bridges) == 4

    def test_links_attached_counts_links_not_queues(self):
        c, _ = self._four(lambda _q: 0)
        c._metrics = MagicMock()
        c.check_liveness(now=1000.0)

        c._metrics.record_amqp_links.assert_called_once_with(4, 0)

    def test_a_silent_link_among_healthy_ones_is_caught(self):
        c, bridges = self._four(lambda _q: 7)
        c.check_liveness(now=0.0)
        for b in bridges[:3]:
            b.deliveries += 10
        c.check_liveness(now=amod._STALL_AFTER_S + 1)

        for b in bridges[:3]:
            b.request_stop.assert_not_called()
        bridges[3].request_stop.assert_called_once_with("watchdog_stall")

    def test_the_queue_depth_is_read_once_per_pass_not_once_per_link(self):
        """Otherwise raising concurrency multiplies management-API calls."""
        calls = []
        c, _ = self._four(lambda q: (calls.append(q), 7)[1])
        c.check_liveness(now=0.0)

        assert calls == [QUEUE]
