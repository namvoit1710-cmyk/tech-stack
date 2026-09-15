"""SA-1490 Phase 2 — WCP sends the pinned worker `spec_version` at dispatch.

The pin (``node.spec_version``, resolved at save by the version pinner) is now
carried on the ``NodeDispatched`` event and included in every task-dispatcher
payload, so the executor can run the version the workflow was authored against.

(Ordering: the executor must TOLERATE the field — SA-1490 Phase 1 — BEFORE this
ships, or the Event-Mesh intake ``ExecuteTaskInput(**data)`` would break.)
"""
from __future__ import annotations

from app.layer1_domain.events import NodeDispatched
from app.layer4_frameworks.messaging.http_task_dispatcher import HttpTaskDispatcher
from app.layer4_frameworks.messaging.event_mesh_task_dispatcher import EventMeshTaskDispatcher
# NB: GrpcTaskDispatcher imports the generated proto (workflow_proto), which
# isn't on the unit-test path here — gRPC's _to_payload spec_version is covered
# in test_grpc_task_dispatcher.py (which sets up that import).


def _ev(spec: str = "2.1.0") -> NodeDispatched:
    return NodeDispatched(task_id="t", run_id="r", node_id="n", worker_type="http-request", spec_version=spec)


def test_node_dispatched_has_spec_version_field():
    assert NodeDispatched().spec_version == ""          # default = unpinned
    assert _ev("3.0.0").spec_version == "3.0.0"


def test_http_payload_carries_spec_version():
    assert HttpTaskDispatcher._to_dispatch_payload(_ev("2.1.0"))["spec_version"] == "2.1.0"
    # dict path (re-dispatch / flattened event)
    pd = HttpTaskDispatcher._to_dispatch_payload({"task_id": "t", "worker_type": "w", "spec_version": "9.9"})
    assert pd["spec_version"] == "9.9"


def test_event_mesh_payload_carries_spec_version():
    # Event-Mesh is the production transport (MESSAGING_MODE=sap).
    assert EventMeshTaskDispatcher._to_dispatch_payload(_ev("2.1.0"))["spec_version"] == "2.1.0"


def test_unpinned_defaults_to_empty_string_everywhere():
    ev = NodeDispatched(task_id="t", worker_type="w")  # no spec_version
    assert HttpTaskDispatcher._to_dispatch_payload(ev)["spec_version"] == ""
    assert EventMeshTaskDispatcher._to_dispatch_payload(ev)["spec_version"] == ""
