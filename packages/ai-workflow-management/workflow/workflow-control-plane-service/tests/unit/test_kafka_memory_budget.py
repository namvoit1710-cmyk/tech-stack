"""SA-2266 — librdkafka's native buffers are bounded instead of defaulted.

The audit finding: ``KafkaBrokerClient`` passed librdkafka nothing but
``bootstrap.servers`` (plus group/offset/commit for the consumer), so every
buffer ran at defaults sized for a server:

* ``queue.buffering.max.kbytes`` 1 GiB — four times the whole 256MB pod
* ``queued.max.messages.kbytes`` 64 MiB
* ``fetch.max.bytes`` 50 MiB per fetch response

These are C-side allocations. They appear in no Python-level cache metric, and
nothing bounded them, so a produce burst could grow ~1 GiB of native buffer on
a 256MB pod — an OOM by configuration rather than by code.
"""

from __future__ import annotations

import pytest

from app.layer4_frameworks.messaging import kafka_memory_budget as kmb
from app.layer4_frameworks.messaging.kafka_memory_budget import (
    apply_buffer_bounds,
    consumer_buffer_config,
    producer_buffer_config,
)

# librdkafka's own documented defaults, for the comparison the ticket asks for.
_DEFAULT_PRODUCE_BACKLOG = 1024 * 1024 * 1024
_DEFAULT_CONSUME_BACKLOG = 64 * 1024 * 1024
_DEFAULT_FETCH_MAX = 50 * 1024 * 1024


class TestLibrdkafkaActuallyAcceptsTheConfig:
    """The test that matters most: a bound librdkafka rejects is a pod that
    does not boot. This caught a real error — ``receive.message.max.bytes``
    must clear ``fetch.max.bytes`` (50 MiB by default), NOT the similarly
    named ``fetch.message.max.bytes`` (1 MiB)."""

    def test_producer_constructs(self):
        from confluent_kafka import Producer

        Producer(apply_buffer_bounds(
            {"bootstrap.servers": "localhost:9092"}, "producer",
        ))

    def test_consumer_constructs(self):
        from confluent_kafka import Consumer

        Consumer(apply_buffer_bounds(
            {"bootstrap.servers": "localhost:9092", "group.id": "g"}, "consumer",
        ))

    def test_the_receive_invariant_holds_for_any_fetch_size(self, monkeypatch):
        """Derived, never hand-typed, so raising fetch.max cannot break boot."""
        from confluent_kafka import Consumer

        for fetch_max in ("1024", "1048576", "16777216", "52428800"):
            monkeypatch.setenv("KAFKA_FETCH_MAX_BYTES", fetch_max)
            conf = consumer_buffer_config()
            assert (
                conf["receive.message.max.bytes"]
                >= conf["fetch.max.bytes"] + 512
            ), f"invariant broken at fetch.max.bytes={fetch_max}"
            Consumer(apply_buffer_bounds(
                {"bootstrap.servers": "localhost:9092", "group.id": "g"},
                "consumer",
            ))


class TestTheBoundsAreSmallerThanTheDefaults:
    def test_produce_backlog_is_bounded_well_under_the_pod(self):
        produce_bytes = producer_buffer_config()["queue.buffering.max.kbytes"] * 1024
        assert produce_bytes < _DEFAULT_PRODUCE_BACKLOG
        assert produce_bytes <= 64 * 1024 * 1024, (
            f"{produce_bytes / 1024 / 1024:.0f}MB of produce backlog on a "
            "256MB pod"
        )

    def test_consume_backlog_and_fetch_are_bounded(self):
        conf = consumer_buffer_config()
        assert conf["queued.max.messages.kbytes"] * 1024 <= _DEFAULT_CONSUME_BACKLOG
        assert conf["fetch.max.bytes"] < _DEFAULT_FETCH_MAX

    def test_total_native_buffer_fits_the_declared_budget(self):
        p, c = producer_buffer_config(), consumer_buffer_config()
        total = (
            p["queue.buffering.max.kbytes"] * 1024
            + c["queued.max.messages.kbytes"] * 1024
        )
        assert total <= kmb.KAFKA_BUFFER_BUDGET_BYTES, (
            f"{total} bytes of backlog against a "
            f"{kmb.KAFKA_BUFFER_BUDGET_BYTES}-byte budget"
        )

    def test_the_budget_drives_the_numbers(self, monkeypatch):
        monkeypatch.setattr(kmb, "KAFKA_BUFFER_BUDGET_BYTES", 8 * 1024 * 1024)
        small = producer_buffer_config()["queue.buffering.max.kbytes"]
        monkeypatch.setattr(kmb, "KAFKA_BUFFER_BUDGET_BYTES", 64 * 1024 * 1024)
        large = producer_buffer_config()["queue.buffering.max.kbytes"]
        assert small * 8 == large


class TestTheCallerAlwaysWins:
    def test_explicit_config_overrides_the_bound(self):
        conf = apply_buffer_bounds(
            {
                "bootstrap.servers": "x",
                "queue.buffering.max.kbytes": 999,
            },
            "producer",
        )
        assert conf["queue.buffering.max.kbytes"] == 999

    def test_bounds_only_fill_gaps(self):
        conf = apply_buffer_bounds({"bootstrap.servers": "x"}, "producer")
        assert conf["bootstrap.servers"] == "x"
        assert "queue.buffering.max.kbytes" in conf

    def test_consumer_keys_are_not_applied_to_a_producer(self):
        conf = apply_buffer_bounds({"bootstrap.servers": "x"}, "producer")
        assert "queued.max.messages.kbytes" not in conf
        assert "fetch.max.bytes" not in conf


class TestBothServicesAreBounded:
    def test_the_control_plane_client_applies_bounds(self):
        import inspect
        from app.layer4_frameworks.messaging import kafka_broker_client

        src = inspect.getsource(kafka_broker_client)
        # Two CALL sites — producer and consumer. The import has no paren.
        assert src.count("apply_buffer_bounds(") == 2, (
            "producer and consumer config must both go through the bounds"
        )

    def test_the_budget_module_is_identical_in_both_services(self):
        """The executor carries its own copy; a drift makes one service
        bounded and the other not."""
        from pathlib import Path

        here = Path(kmb.__file__).resolve()
        # messaging / layer4_frameworks / app / <service> / workflow
        workflow_dir = here.parents[4]
        executor = (
            workflow_dir / "worker-executor-service" / "app"
            / "layer4_frameworks" / "messaging" / "kafka_memory_budget.py"
        )
        assert executor.is_file(), f"executor copy not found at {executor}"
        assert executor.read_text(encoding="utf-8") == here.read_text(
            encoding="utf-8"
        ), "the two copies of the buffer budget have drifted"


class TestConfigParsing:
    def test_garbage_env_does_not_stop_boot(self, monkeypatch):
        monkeypatch.setenv("KAFKA_BUFFER_BUDGET_BYTES", "loads")
        assert kmb._int_env("KAFKA_BUFFER_BUDGET_BYTES", 1234) == 1234

    def test_values_never_go_to_zero(self, monkeypatch):
        monkeypatch.setattr(kmb, "KAFKA_BUFFER_BUDGET_BYTES", 1)
        conf = producer_buffer_config()
        assert conf["queue.buffering.max.kbytes"] >= 1
        assert conf["queue.buffering.max.messages"] >= 1
