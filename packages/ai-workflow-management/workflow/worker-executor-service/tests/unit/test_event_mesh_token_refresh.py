"""Token lifecycle for Event Mesh — the AMQP reconnect-storm regression.

Measured on tenant-1 on 2026-08-16: 40,327 ``token_expiry`` reconnects in 7
days on a single queue, in ~12-hourly bursts peaking at hundreds per minute.
The control plane, doing the same job over six queues, did 48.

The mechanism was entirely local to the two margins:

* ``event_mesh_amqp_consumer._TOKEN_RECONNECT_MARGIN_S`` = 300 — the receiver
  stands its link down 5 minutes before the token's true expiry.
* ``event_mesh_broker_client._TOKEN_REFRESH_MARGIN_S`` = 60 — the cache only
  considered a token stale 1 minute before expiry.

So for the whole 240s between those two points, the reconnect asked for a
token, got the *same* one back, attached a link that was already inside its own
stand-down margin, stood it down on the first loop iteration, and — because the
stand-down path did not sleep — did it again immediately.

These tests pin the invariant that makes it terminate, the proactive refresh
that keeps it from being reached at all, and the backoff that bounds any future
cause of the same shape.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amqp_mod
from app.layer4_frameworks.messaging import event_mesh_broker_client as broker_mod
from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


def _token_response(token: str = "tok-1", expires_in: int = 43200):
    resp = MagicMock()
    resp.json.return_value = {"access_token": token, "expires_in": expires_in}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


class TestMarginInvariant:
    """The two margins must be ordered, or the reconnect cannot terminate."""

    def test_amqp_min_remaining_exceeds_the_reconnect_margin(self):
        # If this ever inverts, every token rotation becomes a storm again.
        assert (
            broker_mod._AMQP_TOKEN_MIN_REMAINING_S
            > amqp_mod._TOKEN_RECONNECT_MARGIN_S
        )

    def test_amqp_min_remaining_tracks_the_same_env_var(self):
        # The two constants live in different modules; they must not be able to
        # drift apart in a deployment that tunes the margin.
        assert broker_mod._AMQP_TOKEN_MIN_REMAINING_S == (
            amqp_mod._TOKEN_RECONNECT_MARGIN_S + 60
        )


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


class TestAmqpTokenIsNeverBornExpiring:

    def test_amqp_get_token_refreshes_inside_the_stand_down_window(self):
        """THE regression. A cached token that the receiver would immediately
        stand down on must be replaced, not handed back."""
        client = _make_client()
        with patch.object(client._http, "post", return_value=_token_response("tok-1")):
            client._ensure_token()
        assert client._access_token == "tok-1"

        # Wind the clock into the window the storm lived in: past the AMQP
        # stand-down margin, but not yet past the plain refresh margin. Before
        # the fix, _ensure_token() returned tok-1 here.
        true_expiry = client._token_expires_at + broker_mod._TOKEN_REFRESH_MARGIN_S
        client._token_expires_at = (
            time.time() + amqp_mod._TOKEN_RECONNECT_MARGIN_S - 30
        )

        with patch.object(
            client._http, "post", return_value=_token_response("tok-2"),
        ) as post:
            token, expires_at = client._amqp_get_token()

        assert post.call_count == 1, "must mint a new token, not reuse the stale one"
        assert token == "tok-2"
        # And the replacement has real life left — enough that the receiver
        # will not stand it down on its first loop iteration.
        assert expires_at - time.time() > amqp_mod._TOKEN_RECONNECT_MARGIN_S
        assert true_expiry  # (kept for readability of the scenario above)

    def test_amqp_get_token_reuses_a_token_with_plenty_of_life(self):
        """The fix must not turn every attach into a token fetch."""
        client = _make_client()
        with patch.object(client._http, "post", return_value=_token_response("tok-1")):
            client._ensure_token()

        with patch.object(client._http, "post") as post:
            token, _ = client._amqp_get_token()

        assert post.call_count == 0
        assert token == "tok-1"

    def test_plain_ensure_token_still_reuses_inside_the_amqp_window(self):
        """Only the AMQP caller needs the wider margin; the management API is
        happy with any unexpired token, and forcing a fetch for it would add
        churn for no benefit."""
        client = _make_client()
        with patch.object(client._http, "post", return_value=_token_response("tok-1")):
            client._ensure_token()
        client._token_expires_at = (
            time.time() + amqp_mod._TOKEN_RECONNECT_MARGIN_S - 30
        )

        with patch.object(client._http, "post") as post:
            assert client._ensure_token() == "tok-1"
        assert post.call_count == 0


# ---------------------------------------------------------------------------
# Proactive refresh
# ---------------------------------------------------------------------------


class TestProactiveRefresh:

    def test_not_due_early_in_the_token_lifetime(self):
        client = _make_client()
        with patch.object(client._http, "post", return_value=_token_response(expires_in=43200)):
            client._ensure_token()
        assert client._token_due_for_refresh() is False

    def test_due_once_the_ratio_is_burned(self):
        client = _make_client()
        with patch.object(client._http, "post", return_value=_token_response(expires_in=43200)):
            client._ensure_token()
        # 76% of a 12h token elapsed.
        client._token_issued_at = time.time() - 43200 * 0.76
        assert client._token_due_for_refresh() is True

    def test_due_when_no_token_has_ever_been_minted(self):
        assert _make_client()._token_due_for_refresh() is True

    def test_refresh_thread_is_idempotent_and_stops_on_close(self):
        client = _make_client()
        client._start_token_refresh()
        first = client._token_refresh_thread
        client._start_token_refresh()
        assert client._token_refresh_thread is first
        assert first is not None and first.daemon is True

        with patch.object(client._http, "close"):
            client.close()
        assert client._token_refresh_thread is None
        assert client._token_refresh_stop.is_set()

    def test_subscribe_starts_the_refresh_thread(self):
        """Consumption is the path the storm lived on — the thread must be
        running by the time any link attaches."""
        client = _make_client(consume_transport="amqp")
        with patch.object(client, "_provision_and_qualify", return_value="q"), \
                patch.object(client, "_register"), \
                patch.object(client, "_start_amqp_consumer", return_value=True), \
                patch.object(client, "_start_dmq_watch"):
            client.subscribe_to_topic("workflow.tasks", lambda *_: None)
        assert client._token_refresh_thread is not None
        client._token_refresh_stop.set()


# ---------------------------------------------------------------------------
# The metric that was missing entirely
# ---------------------------------------------------------------------------


class TestTokenRefreshMetric:

    def test_success_is_counted(self):
        client = _make_client()
        monitor = MagicMock()
        client.set_monitor(monitor)
        with patch.object(client._http, "post", return_value=_token_response()):
            client._ensure_token()
        monitor.eventmesh_token_refresh.assert_called_once_with("success")

    def test_failure_is_counted_and_still_raises(self):
        client = _make_client()
        monitor = MagicMock()
        client.set_monitor(monitor)
        with patch.object(client._http, "post", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                client._ensure_token()
        monitor.eventmesh_token_refresh.assert_called_once_with("failure")

    def test_metric_failure_never_breaks_token_acquisition(self):
        """Reporting must never be the thing that stops consumption."""
        client = _make_client()
        monitor = MagicMock()
        monitor.eventmesh_token_refresh.side_effect = RuntimeError("metrics down")
        client.set_monitor(monitor)
        with patch.object(client._http, "post", return_value=_token_response("tok-9")):
            assert client._ensure_token() == "tok-9"


# ---------------------------------------------------------------------------
# The reconnect path itself — defence in depth
# ---------------------------------------------------------------------------


def _drive_receive_loop(*, reason: str, session_s: float, iterations: int):
    """Run ``_receive_loop`` against a bridge that always ends the same way.

    Returns ``(sleeps, attempts, metrics)``. ``sleeps`` is what the loop asked
    to wait for — pre-fix, a stand-down path produced an EMPTY list no matter
    how fast the link came back, which is the storm.
    """
    metrics = MagicMock()
    consumer = amqp_mod.EventMeshAMQPConsumer(
        host="broker.example.com",
        get_token=lambda: ("tok", time.time() + 86400),
        message_handler=lambda _q, _m: True,
        metrics=metrics,
    )
    consumer._running = True
    sleeps: list[float] = []
    attempts = {"n": 0}

    class _FakeBridge:
        def __init__(self, **_kw):
            self.stood_down = None
            self.error = None
            self.ready = True
            self.attached_at = 0.0

        def run(self, deadline=None):
            attempts["n"] += 1
            self.attached_at = time.monotonic() - session_s
            self.stood_down = reason
            if attempts["n"] >= iterations:
                consumer._running = False

    with patch.object(amqp_mod, "_ProtonWsBridge", _FakeBridge), \
            patch.object(consumer, "_sleep_interruptible", sleeps.append):
        consumer._receive_loop("simplemdg/event/core-2/sub-workflow-tasks-amqp", 0)
    return sleeps, attempts["n"], metrics


class TestStandDownIsRateLimited:

    def test_rotation_after_a_healthy_session_reconnects_immediately(self):
        """The designed path must stay fast — a delay here delays task intake."""
        sleeps, attempts, metrics = _drive_receive_loop(
            reason="token_expiry",
            session_s=amqp_mod._HEALTHY_SESSION_S + 1,
            iterations=4,
        )
        assert sleeps == [], "a healthy link that rotated its token must not back off"
        assert attempts == 4
        assert metrics.record_amqp_reconnect.call_args[0][1] == "token_expiry"

    def test_instant_stand_downs_are_backed_off(self):
        """THE storm guard. A link that stands down before doing any work must
        not be retried at full speed."""
        sleeps, _attempts, _metrics = _drive_receive_loop(
            reason="token_expiry", session_s=0.0, iterations=4,
        )
        assert len(sleeps) == 3, "every unproductive stand-down must wait"
        assert all(s > 0 for s in sleeps)
        # Exponential: 1s, 2s, 4s, each jittered by ±50%.
        assert sleeps[0] <= amqp_mod._RECONNECT_BASE_S * 1.5
        assert sleeps[2] >= amqp_mod._RECONNECT_BASE_S * 4 * 0.5

    def test_a_run_of_them_trips_the_breaker(self):
        sleeps, _attempts, _metrics = _drive_receive_loop(
            reason="token_expiry",
            session_s=0.0,
            iterations=amqp_mod._STAND_DOWN_STORM_TRIP + 1,
        )
        assert len(sleeps) == amqp_mod._STAND_DOWN_STORM_TRIP
        assert sleeps[-1] == amqp_mod._STAND_DOWN_COOL_OFF_S, (
            "the breaker holds the link down for a fixed, un-jittered cool-off"
        )

    def test_the_streak_resets_after_a_link_that_lasted(self):
        """A single slow rotation must not poison the next one."""
        metrics = MagicMock()
        consumer = amqp_mod.EventMeshAMQPConsumer(
            host="broker.example.com",
            get_token=lambda: ("tok", time.time() + 86400),
            message_handler=lambda _q, _m: True,
            metrics=metrics,
        )
        consumer._running = True
        sleeps: list[float] = []
        # short, short, LONG, short  -> the third resets the streak, so the
        # fourth is charged as a first offence again.
        sessions = [0.0, 0.0, amqp_mod._HEALTHY_SESSION_S + 1, 0.0]
        seen = {"n": 0}

        class _FakeBridge:
            def __init__(self, **_kw):
                self.stood_down = None
                self.error = None
                self.ready = True
                self.attached_at = 0.0

            def run(self, deadline=None):
                i = seen["n"]
                seen["n"] += 1
                self.attached_at = time.monotonic() - sessions[i]
                self.stood_down = "token_expiry"
                if seen["n"] >= len(sessions):
                    consumer._running = False

        with patch.object(amqp_mod, "_ProtonWsBridge", _FakeBridge), \
                patch.object(consumer, "_sleep_interruptible", sleeps.append):
            consumer._receive_loop("q", 0)

        # Two short ones backed off (1s, 2s jittered); the long one did not.
        assert len(sleeps) == 2
        assert sleeps[1] > sleeps[0] * 0.5


class TestJitter:

    def test_stays_within_half_and_one_and_a_half(self):
        for _ in range(200):
            assert 2.5 <= amqp_mod._jittered(5.0) <= 7.5

    def test_is_capped_at_the_ceiling(self):
        for _ in range(200):
            assert amqp_mod._jittered(amqp_mod._RECONNECT_MAX_S) <= amqp_mod._RECONNECT_MAX_S

    def test_never_negative(self):
        assert amqp_mod._jittered(-1.0) == 0.0
