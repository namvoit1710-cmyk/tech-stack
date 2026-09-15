"""SA-1470 — REST-consumer 429 isolation + fallback observability.

The story's acceptance criteria, as tests:
1. Falling back to REST raises an alert — transport gauge + fallback counter
   fire at the decision points (proton missing / AMQP unreachable / poll
   loop start).
2. A 429 on one queue no longer stalls other queues under "queue" scope —
   and under the default "instance" scope (the broker's actual limit) the
   pause is alertable and time-bounded.
3. EVENT_MESH_REQUIRE_AMQP gates readiness: a pod forced onto the REST
   fallback reports 503; default off keeps behaviour unchanged.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging import event_mesh_consumer as emc
from app.layer4_frameworks.messaging.event_mesh_consumer import EventMeshConsumer
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames


def _make_consumer(**overrides) -> tuple[EventMeshConsumer, MagicMock]:
    metrics = overrides.pop("metrics", MagicMock())
    defaults = dict(
        messaging_url="https://messaging.example.com",
        names=EventMeshNames("ns"),
        tokens=MagicMock(),
        admin=MagicMock(),
        clients=MagicMock(),
        poll_interval_s=0.05,
        lock_duration_ms=60_000,
        handler_timeout_s=None,
        consume_transport="rest",
        amqp_host="",
        amqp_port=443,
        amqp_ws_path="/protocols/amqp10ws",
        amqp_prefetch=50,
        amqp_concurrency=1,
        amqp_strip_namespace=False,
        metrics=metrics,
    )
    defaults.update(overrides)
    return EventMeshConsumer(**defaults), metrics


def _resp(status: int, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    return resp


class TestFallbackObservability:
    def test_poll_loop_start_emits_rest_gauge(self):
        consumer, metrics = _make_consumer()
        with patch.object(consumer, "_poll_loop"):
            consumer._start_poll_loop("q1")
        metrics.record_consume_transport.assert_called_once_with("rest")
        consumer.close()

    def test_proton_missing_emits_fallback_counter(self):
        consumer, metrics = _make_consumer(consume_transport="amqp", amqp_host="h")
        with patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_available",
            return_value=False,
        ), patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_import_error",
            return_value="no proton",
        ):
            assert consumer._start_amqp_consumer("q") == emc.ATTACH_NO_LIBS
        metrics.record_consume_fallback.assert_called_once_with("proton_missing")
        # ``_amqp_state`` is pinned to "rest" only when the runtime fallback is
        # enabled — otherwise there is nothing to pin to, and pinning would
        # stop ``retry_stood_down`` from ever re-probing. The reason is still
        # recorded, which is what the counter above is about.
        assert consumer._amqp_state is None
        assert consumer._amqp_fallback_reason == emc.ATTACH_NO_LIBS

    def test_amqp_unreachable_emits_fallback_counter(self):
        consumer, metrics = _make_consumer(consume_transport="amqp", amqp_host="h")
        fake_consumer = MagicMock()
        fake_consumer.probe.return_value = False
        with patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_available",
            return_value=True,
        ), patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.EventMeshAMQPConsumer",
            return_value=fake_consumer,
        ):
            assert consumer._start_amqp_consumer("q") == emc.ATTACH_UNREACHABLE
        metrics.record_consume_fallback.assert_called_once_with("amqp_unreachable")

    def test_transport_state_reports_rest_fallback(self):
        consumer, _ = _make_consumer(consume_transport="amqp", amqp_host="h")
        consumer._amqp_state = "rest"
        state = consumer.transport_state()
        assert state["configured"] == "amqp"
        assert state["active"] == "rest"
        assert state["rest_fallback"] is True

    def test_transport_state_amqp_active_not_fallback(self):
        consumer, _ = _make_consumer(consume_transport="amqp", amqp_host="h")
        consumer._amqp_state = "amqp"
        state = consumer.transport_state()
        assert state["active"] == "amqp"
        assert state["rest_fallback"] is False


class TestCooldownScope:
    def test_instance_scope_one_429_pauses_all_queues(self):
        consumer, metrics = _make_consumer(cooldown_scope="instance")
        consumer._note_consume_rate_limited("q-noisy", _resp(429))
        assert consumer._cooldown_remaining("q-noisy") > 0
        assert consumer._cooldown_remaining("q-other") > 0  # shared pause
        metrics.record_consume_rate_limited.assert_called_once()
        args = metrics.record_consume_rate_limited.call_args[0]
        assert args[0] == "q-noisy" and args[3] == "instance"

    def test_queue_scope_only_throttled_queue_pauses(self):
        consumer, metrics = _make_consumer(cooldown_scope="queue")
        consumer._note_consume_rate_limited("q-noisy", _resp(429))
        assert consumer._cooldown_remaining("q-noisy") > 0
        assert consumer._cooldown_remaining("q-other") == 0.0  # isolated
        args = metrics.record_consume_rate_limited.call_args[0]
        assert args[3] == "queue"

    def test_queue_scope_other_queue_still_consumed(self):
        """AC: a 429 on one queue no longer stalls the other's consumption."""
        consumer, _ = _make_consumer(cooldown_scope="queue")
        consumer._tokens.auth_headers.return_value = {}
        calls: list[str] = []

        def fake_post(url, **_kw):
            calls.append(url)
            if "q-noisy" in url:
                return _resp(429)
            resp = _resp(204)  # empty but responsive
            return resp

        consumer._clients.sync = MagicMock()
        consumer._clients.sync.post.side_effect = fake_post

        assert consumer.consume_from_queue("q-noisy") is None  # 429 → cooldown
        assert consumer.consume_from_queue("q-other") is None  # 204 → polled fine
        assert any("q-other" in u for u in calls)  # other queue actually hit

    def test_retry_after_header_honored(self):
        consumer, metrics = _make_consumer()
        consumer._note_consume_rate_limited("q", _resp(429, {"Retry-After": "7"}))
        remaining = consumer._cooldown_remaining("q")
        assert 6.0 < remaining <= 7.0
        assert metrics.record_consume_rate_limited.call_args[0][1] == 7.0

    def test_backoff_grows_and_is_capped(self):
        consumer, metrics = _make_consumer()
        durations = []
        for _ in range(8):
            consumer._note_consume_rate_limited("q", _resp(429))
            durations.append(metrics.record_consume_rate_limited.call_args[0][1])
            # clear the deadline but keep the streak growing
            with consumer._cooldown_lock:
                deadline, streak = consumer._cooldowns[consumer._cooldown_key("q")]
                consumer._cooldowns[consumer._cooldown_key("q")] = (0.0, streak)
        assert durations[0] == 1.0
        assert durations[1] == 2.0
        assert max(durations) <= 30.0  # time-bounded (AC)
        assert durations[-1] == 30.0

    def test_success_resets_cooldown_streak(self):
        consumer, _ = _make_consumer()
        consumer._note_consume_rate_limited("q", _resp(429))
        consumer._clear_cooldown("q")
        assert consumer._cooldown_remaining("q") == 0.0
        # next 429 starts the backoff ladder from the base again
        metrics2 = consumer._metrics
        consumer._note_consume_rate_limited("q", _resp(429))
        assert metrics2.record_consume_rate_limited.call_args[0][1] == 1.0


class TestBrokerFacadeExposesConsumeState:
    def _make_client(self) -> EventMeshBrokerClient:
        return EventMeshBrokerClient(EventMeshConfig(
            token_url="https://a", messaging_url="https://m",
            management_url="https://g", client_id="i", client_secret="s",
            namespace="ns", consume_transport="rest",
        ))

    def test_health_check_includes_consume(self):
        client = self._make_client()
        try:
            health = client.health_check()
            assert health["consume"]["configured"] == "rest"
            assert client.consume_transport_state()["cooldown_scope"] == "instance"
        finally:
            client.close()

    def test_config_scope_wires_through(self):
        client = EventMeshBrokerClient(EventMeshConfig(
            token_url="https://a", messaging_url="https://m",
            management_url="https://g", client_id="i", client_secret="s",
            namespace="ns", consume_429_scope="QUEUE",
        ))
        try:
            assert client.consume_transport_state()["cooldown_scope"] == "queue"
        finally:
            client.close()


class TestReadinessGate:
    class _Req:
        def __init__(self, container):
            self.app = type("A", (), {"state": type("S", (), {"container": container})()})()

    class _StubBroker:
        def __init__(self, rest_fallback):
            self._fb = rest_fallback

        def consume_transport_state(self):
            return {"configured": "amqp", "active": "rest" if self._fb else "amqp",
                    "rest_fallback": self._fb, "cooldown_scope": "instance",
                    "active_cooldowns": {}}

    def _call(self, container):
        from fastapi import Response
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        resp = Response()
        body = health_controller.readiness_check(self._Req(container), resp)
        return body, resp.status_code

    def _container(self, rest_fallback):
        infra = type("I", (), {"broker": self._StubBroker(rest_fallback)})()
        return {"_infra": infra, "_response_topic_listener": None}

    def test_gate_off_rest_fallback_stays_ready(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings, "EVENT_MESH_REQUIRE_AMQP", False, raising=False)
        body, status = self._call(self._container(rest_fallback=True))
        assert body["ready"] is True and status == 200
        assert body["event_mesh_consume"]["rest_fallback"] is True  # surfaced

    def test_gate_on_rest_fallback_returns_503(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings, "EVENT_MESH_REQUIRE_AMQP", True, raising=False)
        body, status = self._call(self._container(rest_fallback=True))
        assert body["ready"] is False and status == 503
        assert "event_mesh_rest_fallback" in body["reasons"]

    def test_gate_on_amqp_active_is_200(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings, "EVENT_MESH_REQUIRE_AMQP", True, raising=False)
        body, status = self._call(self._container(rest_fallback=False))
        assert body["ready"] is True and status == 200
