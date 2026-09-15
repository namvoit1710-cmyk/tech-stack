"""Unit tests for webhook convergence (SA-2059 B7/B8, B10).

The ingress front door verifies a registration, normalizes via the source
adapter, and PUBLISHES onto the registration's emit_topic — never StartRun.
Covers: unregistered 404, secret/HMAC verification, publish→topic, Jira
normalization + self-trigger loop guard, and the deliveries log.
"""

import hashlib
import hmac
import json

import pytest

from app.layer1_domain.entities.webhook_registration import WebhookRegistration
from app.layer1_domain.value_objects.event_id import EventId
from app.layer4_frameworks.persistence.memory.memory_webhook_registration_repo import (
    MemoryWebhookRegistrationRepository,
)
from app.layer2_application.services.webhook_ingress.ingress_service import (
    WebhookIngressService,
)
from app.layer2_application.services.webhook_ingress import source_adapters

pytestmark = pytest.mark.unit


class _Bus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def __call__(self, topic, message):
        self.published.append((topic, message))


def _repo_with(reg: WebhookRegistration):
    repo = MemoryWebhookRegistrationRepository()
    repo.save(reg)
    return repo


def _reg(**kw):
    defaults = dict(name="jira-src", path="/hooks/jira", source="jira",
                    secret="s3cret", emit_topic="mdg.part.created", tenant_id="acme")
    defaults.update(kw)
    return WebhookRegistration(**defaults)


# ── routing / registration ────────────────────────────────────────────

def test_unregistered_path_returns_404_and_publishes_nothing():
    bus = _Bus()
    svc = WebhookIngressService(MemoryWebhookRegistrationRepository(), bus)
    res = svc.ingest(path="/hooks/none", body={}, headers={}, raw_body=b"{}")
    assert res.status_code == 404
    assert bus.published == []


def test_disabled_registration_returns_404():
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(_reg(enabled=False)), bus)
    res = svc.ingest(path="/hooks/jira", body={}, headers={"x-webhook-secret": "s3cret"}, raw_body=b"{}")
    assert res.status_code == 404


# ── verification ───────────────────────────────────────────────────────

def test_bad_secret_rejected_401():
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(_reg()), bus)
    res = svc.ingest(path="/hooks/jira", body={}, headers={"x-webhook-secret": "wrong"}, raw_body=b"{}")
    assert res.status_code == 401
    assert bus.published == []


def test_hmac_signature_verified():
    reg = _reg(secret="", hmac_secret="hs")
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(reg), bus)
    body = {"issue": {"key": "MDG-1", "fields": {"updated": "t0"}}, "webhookEvent": "jira:issue_created"}
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"hs", raw, hashlib.sha256).hexdigest()

    ok = svc.ingest(path="/hooks/jira", body=body, headers={"x-hub-signature-256": sig}, raw_body=raw)
    assert ok.status_code == 202 and ok.published == 1

    bad = svc.ingest(path="/hooks/jira", body=body,
                     headers={"x-hub-signature-256": "sha256=deadbeef"}, raw_body=raw)
    assert bad.status_code == 401


def test_hmac_accepts_jira_native_x_hub_signature_header():
    # Jira Cloud native WebHook sends ``X-Hub-Signature`` (no ``-256`` suffix).
    reg = _reg(secret="", hmac_secret="hs")
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(reg), bus)
    body = {"issue": {"key": "MDG-1", "fields": {"updated": "t0"}}, "webhookEvent": "jira:issue_created"}
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"hs", raw, hashlib.sha256).hexdigest()

    ok = svc.ingest(path="/hooks/jira", body=body, headers={"x-hub-signature": sig}, raw_body=raw)
    assert ok.status_code == 202 and ok.published == 1


def test_ip_allowlist_enforced():
    reg = _reg(ip_allowlist=["10.0.0.1"])
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(reg), bus)
    res = svc.ingest(path="/hooks/jira", body={}, headers={"x-webhook-secret": "s3cret"},
                     raw_body=b"{}", client_ip="8.8.8.8")
    assert res.status_code == 401


# ── normalize + publish ────────────────────────────────────────────────

def test_verified_event_publishes_envelope_to_topic():
    reg = _reg()
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(reg), bus)
    body = {"issue": {"key": "MDG-7", "fields": {"updated": "2026-07-16"}},
            "webhookEvent": "jira:issue_created"}
    res = svc.ingest(path="/hooks/jira", body=body, headers={"x-webhook-secret": "s3cret"},
                     raw_body=json.dumps(body).encode())

    assert res.status_code == 202 and res.published == 1
    topic, envelope = bus.published[0]
    assert topic == "mdg.part.created"
    assert envelope["event_topic"] == "mdg.part.created"
    assert envelope["tenant_id"] == "acme"          # from registration, not payload
    assert envelope["source"] == "jira"
    # stable id: derived from (issue key, event, updated)
    assert envelope["event_id"] == EventId.derive("jira:MDG-7", "jira:issue_created:2026-07-16")


def test_jira_self_trigger_loop_guard_drops_event():
    reg = _reg(integration_actor="acct-bot")
    bus = _Bus()
    svc = WebhookIngressService(_repo_with(reg), bus)
    body = {"issue": {"key": "MDG-1", "fields": {"updated": "t"}},
            "webhookEvent": "jira:issue_updated",
            "user": {"accountId": "acct-bot"}}
    res = svc.ingest(path="/hooks/jira", body=body, headers={"x-webhook-secret": "s3cret"},
                     raw_body=json.dumps(body).encode())

    assert res.status_code == 202 and res.outcome == "dropped_loop"
    assert bus.published == []


def test_generic_adapter_passthrough_uses_registration_topic():
    events = source_adapters.normalize(
        "generic", {"any": "payload"}, {"x-event-id": "e1"},
        _reg(source="generic", emit_topic="custom.topic"),
    )
    assert events is not None and len(events) == 1
    assert events[0].event_topic == "custom.topic"
    assert events[0].event_id == "e1"


# ── deliveries log ─────────────────────────────────────────────────────

def test_deliveries_are_recorded_newest_first():
    reg = _reg()
    repo = _repo_with(reg)
    bus = _Bus()
    svc = WebhookIngressService(repo, bus)
    for i in range(3):
        body = {"issue": {"key": f"MDG-{i}", "fields": {"updated": str(i)}}, "webhookEvent": "jira:issue_created"}
        svc.ingest(path="/hooks/jira", body=body, headers={"x-webhook-secret": "s3cret"},
                   raw_body=json.dumps(body).encode())

    deliveries = repo.list_deliveries(reg.id)
    assert len(deliveries) == 3
    assert all(d.outcome == "published" for d in deliveries)
    # the raw inbound body is captured so the FE can show it
    assert all('"issue"' in d.payload for d in deliveries)
    # newest first
    assert deliveries[0].event_id == EventId.derive("jira:MDG-2", "jira:issue_created:2")
    # get_delivery fetches one by id (incl. payload); unknown id -> None
    one = repo.get_delivery(reg.id, deliveries[0].id)
    assert one is not None and one.payload == deliveries[0].payload
    assert repo.get_delivery(reg.id, "nope") is None


# ── ingress → consumer topic contract (SA-2059 integration regression) ──

def test_ingress_event_reaches_consumer_subscribed_to_canonical_topic():
    """An ingress event must reach the EventTriggerConsumer, which subscribes
    ONLY to the single canonical inbox ``TRIGGERS_TOPIC``. The registration's
    ``emit_topic`` is a LOGICAL routing key carried in the envelope (matched
    against each workflow's Event trigger), NOT the physical broker topic.

    Regression: the ingress used to publish onto ``emit_topic`` as the physical
    topic, so the consumer (subscribed to ``workflow.triggers``) never received
    it — ingress returned 202 "published" but no run ever started. Here the
    physical topic (canonical) differs from the logical topic, and the run must
    still start. Locks the wiring contract, not a single layer in isolation.
    """
    import asyncio
    from types import SimpleNamespace

    from app.layer4_frameworks.messaging.event_trigger_consumer import (
        EventTriggerConsumer, TRIGGERS_TOPIC,
    )
    from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
        MemoryWorkflowRepository,
    )
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer1_domain.value_objects.node_type import NodeType
    from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
        StartRunResult,
    )

    LOGICAL = "mdg.part.created"           # emit_topic — must NOT equal TRIGGERS_TOPIC
    assert LOGICAL != TRIGGERS_TOPIC

    from app.layer1_domain.value_objects.workflow_status import WorkflowStatus

    wf_repo = MemoryWorkflowRepository()
    node = WorkflowNode(workflow_id="wf-a", node_type=NodeType.TRIGGER,
                        parameters={"trigger_type": "event", "event_topic": LOGICAL})
    # PUBLISHED, not the DRAFT default: an inbound webhook can only reach a
    # workflow whose event trigger has actually been published.
    wf_repo.save(Workflow(id="wf-a", workflow_id="wf-a", name="wf-a", nodes=[node],
                          main_flow=True, status=WorkflowStatus.PUBLISHED))

    started: list[str] = []

    class _StartRun:
        async def execute(self, cmd):
            started.append(cmd.workflow_id)
            return StartRunResult(id=cmd.predefined_run_id, workflow_id=cmd.workflow_id, status="RUNNING")

    class _RunRepo:
        async def aget_by_id(self, rid):
            return None

        async def alist_runs(self, filters):
            return SimpleNamespace(items=[], total=0)

    routed: dict[str, list] = {}

    class _RoutingBroker:
        """Delivers a publish only to callbacks subscribed to that exact topic."""

        def subscribe_to_topic(self, topic, cb):
            routed.setdefault(topic, []).append(cb)

        def publish_to_topic(self, topic, msg):
            for cb in routed.get(topic, []):
                cb(msg)

        def ensure_topic(self, topic):
            pass

    broker = _RoutingBroker()
    consumer = EventTriggerConsumer(
        {"workflow_repo": wf_repo, "run_repo": _RunRepo(), "start_run": _StartRun(), "monitor": None},
        broker=broker, condition_evaluator=None,
    )
    broker.subscribe_to_topic(TRIGGERS_TOPIC, lambda m: asyncio.run(consumer._handle(m)))

    # Ingress wired exactly like bootstrap: physical topic is the canonical inbox.
    reg = _reg(secret="", hmac_secret="hs", emit_topic=LOGICAL)
    svc = WebhookIngressService(
        _repo_with(reg),
        lambda topic, message: broker.publish_to_topic(TRIGGERS_TOPIC, message),
    )
    body = {"issue": {"key": "MDG-1", "fields": {"updated": "t0"}}, "webhookEvent": "jira:issue_updated"}
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"hs", raw, hashlib.sha256).hexdigest()
    res = svc.ingest(path="/hooks/jira", body=body, headers={"x-hub-signature": sig}, raw_body=raw)

    assert res.status_code == 202
    assert started == ["wf-a"]  # started despite emit_topic != physical topic
