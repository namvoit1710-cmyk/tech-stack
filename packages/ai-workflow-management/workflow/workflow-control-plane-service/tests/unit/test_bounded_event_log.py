"""M2 (SA-2259): the AMQP handshake diagnostic must be bounded, not a leak.

``_ProtonWsBridge.events_seen`` was a plain ``list`` appended once per proton
event and never trimmed. On the probe path the bridge is short-lived so it
stayed small — which is exactly why the leak survived SA-1861 — but on the
receive path the bridge lives as long as the connection, so the list grew with
traffic for the whole life of the pod.

These tests pin down BOTH halves of the fix:

* memory is bounded no matter how much traffic flows through, and
* the diagnostic at ``_probe_once``'s warning still answers the question it
  exists to answer — how far did the handshake get — which a plain
  ``deque(maxlen=N)`` would have destroyed by dropping the head.

The identical suite ships in worker-executor-service, which carries its own
copy of the consumer module. That copy is not optional: it is the half SA-1861
never covered.
"""

from __future__ import annotations

import sys

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amqp_mod
from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import (
    _BoundedEventLog,
    _int_env,
)

# One million events, as the acceptance criterion asks for. Kept as a module
# constant so the "bounded" assertions can be phrased against it.
FLOOD = 1_000_000


def _flood(log: _BoundedEventLog, n: int = FLOOD, prefix: str = "EV") -> None:
    for i in range(n):
        log.append(f"{prefix}{i % 7}")


class TestMemoryIsBounded:
    """The leak itself."""

    def test_retained_entries_do_not_grow_with_traffic(self):
        log = _BoundedEventLog(head=32, tail=32)
        _flood(log)
        retained = len(list(log))
        assert retained <= 64, (
            f"{retained} entries retained after {FLOOD} events — the log grows "
            "with traffic, which is the leak this fixes"
        )

    def test_a_hundredfold_increase_in_traffic_costs_nothing(self):
        small = _BoundedEventLog(head=8, tail=8)
        large = _BoundedEventLog(head=8, tail=8)
        _flood(small, 10_000)
        _flood(large, 1_000_000)
        # Same object shape, same footprint — the whole point.
        assert len(list(small)) == len(list(large))
        assert _footprint(small) == _footprint(large)

    def test_counter_key_space_is_capped(self):
        """A pathological ``str(etype)`` must not become a second unbounded dict."""
        log = _BoundedEventLog(head=4, tail=4, kinds=16)
        for i in range(10_000):
            log.append(f"UNIQUE_KIND_{i}")
        counts = log.counts()
        assert len(counts) <= 17, (
            f"{len(counts)} distinct keys retained from 10000 distinct names — "
            "the tally is unbounded"
        )
        assert counts.get("OTHER", 0) > 0, "overflow names must land in the OTHER bucket"
        assert sum(counts.values()) == 10_000, "every event must still be counted"

    def test_clear_releases_everything(self):
        log = _BoundedEventLog(head=8, tail=8)
        _flood(log, 5_000)
        log.clear()
        assert list(log) == []
        assert log.total == 0
        assert log.counts() == {}


class TestDiagnosticIsPreserved:
    """The half a naive deque(maxlen=N) would have broken."""

    def test_handshake_prefix_survives_a_flood(self):
        """The FIRST events answer 'how far did the handshake get'."""
        log = _BoundedEventLog(head=4, tail=4)
        for name in ("CONNECTION_INIT", "CONNECTION_BOUND",
                     "CONNECTION_REMOTE_OPEN", "SESSION_REMOTE_OPEN"):
            log.append(name)
        _flood(log, 100_000, prefix="DELIVERY")
        assert list(log)[:4] == [
            "CONNECTION_INIT", "CONNECTION_BOUND",
            "CONNECTION_REMOTE_OPEN", "SESSION_REMOTE_OPEN",
        ], "the handshake prefix was evicted — the diagnostic is now blind"

    def test_tail_holds_the_most_recent_events(self):
        log = _BoundedEventLog(head=2, tail=3)
        for name in ["A", "B", "C", "D", "E", "F"]:
            log.append(name)
        assert list(log) == ["A", "B", "D", "E", "F"]

    def test_repr_is_a_plain_list_while_small(self):
        """A short probe must log EXACTLY what it logged before this change."""
        log = _BoundedEventLog(head=32, tail=32)
        for name in ("CONNECTION_INIT", "TRANSPORT_ERROR"):
            log.append(name)
        assert repr(log) == repr(["CONNECTION_INIT", "TRANSPORT_ERROR"])
        assert f"{log}" == "['CONNECTION_INIT', 'TRANSPORT_ERROR']"

    def test_empty_log_still_reads_as_empty(self):
        """``events=[] recv=0`` is a documented diagnosis — keep it verbatim."""
        assert repr(_BoundedEventLog()) == "[]"
        assert f"{_BoundedEventLog()}" == "[]"

    def test_repr_says_how_much_it_elided(self):
        log = _BoundedEventLog(head=2, tail=2)
        _flood(log, 1_000)
        rendered = repr(log)
        assert "996 more" in rendered, rendered

    def test_total_counts_every_event_including_dropped_ones(self):
        log = _BoundedEventLog(head=2, tail=2)
        _flood(log, 50_000)
        assert log.total == 50_000
        assert len(log) == 50_000, "len() reports events seen, not entries retained"

    def test_counts_survive_eviction(self):
        log = _BoundedEventLog(head=1, tail=1)
        for _ in range(100):
            log.append("DELIVERY")
        for _ in range(5):
            log.append("TRANSPORT_ERROR")
        assert log.counts() == {"DELIVERY": 100, "TRANSPORT_ERROR": 5}


class TestDropInCompatibility:
    """Nothing that used the old plain list should notice."""

    def test_equals_the_list_it_replaced(self):
        log = _BoundedEventLog()
        for name in ("A", "B"):
            log.append(name)
        assert log == ["A", "B"]
        assert log != ["A", "C"]

    def test_membership_and_iteration(self):
        log = _BoundedEventLog(head=2, tail=2)
        for name in ("A", "B", "C", "D"):
            log.append(name)
        assert "A" in log and "D" in log
        assert list(iter(log)) == ["A", "B", "C", "D"]

    def test_bridge_starts_with_a_bounded_log(self):
        bridge = amqp_mod._ProtonWsBridge(
            url="wss://example.invalid/protocols/amqp10ws",
            address="queue:q",
            queue_name="q",
            token="tok",
            prefetch=10,
            message_handler=lambda q, d: True,
            is_running=lambda: False,
            hostname="example.invalid",
        )
        assert isinstance(bridge.events_seen, _BoundedEventLog), (
            "the bridge still allocates a plain list — the leak is back"
        )
        # And the append site the receive loop uses stays bounded.
        _flood(bridge.events_seen, 200_000)
        assert len(list(bridge.events_seen)) <= 64


class TestConfigParsing:
    def test_int_env_reads_a_value(self, monkeypatch):
        monkeypatch.setenv("SA2259_PROBE", "17")
        assert _int_env("SA2259_PROBE", 5) == 17

    def test_int_env_falls_back_on_garbage(self, monkeypatch):
        monkeypatch.setenv("SA2259_PROBE", "not-a-number")
        assert _int_env("SA2259_PROBE", 5) == 5

    def test_int_env_falls_back_when_unset(self, monkeypatch):
        monkeypatch.delenv("SA2259_PROBE", raising=False)
        assert _int_env("SA2259_PROBE", 5) == 5

    def test_int_env_honours_the_minimum(self, monkeypatch):
        monkeypatch.setenv("SA2259_PROBE", "0")
        assert _int_env("SA2259_PROBE", 5, minimum=1) == 1


def _footprint(log: _BoundedEventLog) -> int:
    """Bytes held by the log's own containers (not the interned name strings)."""
    return (
        sys.getsizeof(log._head)
        + sys.getsizeof(log._tail)
        + sys.getsizeof(log._counts)
    )


@pytest.mark.parametrize("events", [10, 1_000, 100_000, FLOOD])
def test_footprint_is_flat_across_four_orders_of_magnitude(events):
    """Not just 'bounded' — the same, so an RSS slope of ~0 is provable (G2)."""
    log = _BoundedEventLog(head=16, tail=16, kinds=8)
    _flood(log, events)
    baseline = _BoundedEventLog(head=16, tail=16, kinds=8)
    _flood(baseline, 64)
    assert _footprint(log) <= _footprint(baseline) * 2, (
        f"footprint grew with {events} events — G2 (RSS slope ~ 0) cannot hold"
    )
