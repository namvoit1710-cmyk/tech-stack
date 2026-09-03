"""SA-1734 T3 — worker→executor registries must carry ``kind``.

Two registries feed the executor:
  * HTTP  (``http_worker_registry.HttpWorkerRegistry``) — POSTs a JSON payload.
  * gRPC  (``grpc_worker_registry.GrpcWorkerRegistry``) — builds a
    ``RegisterWorkerRequest`` protobuf.

Both must forward ``WorkerRegistration.kind`` so the executor/registry can
persist the node's connector kind.

Code-review follow-up (kind-as-Enum-everywhere): ``WorkerRegistration.kind``
is now ``NodeKind(str, Enum)``, not a raw string. Both tests below assert on
the *serialised* wire value -- ``"read"``, never the enum repr
(``"NodeKind.READ"``) -- proving the ``str, Enum`` mix-in stays wire-compatible
across both transports.

SA-1734 blocker fix: the gRPC test below used to hand-build the protobuf
request itself, copying the production expression
(``kind=getattr(reg, "kind", "action") or "action"``) from
``grpc_worker_registry.py`` character-for-character — it never called
``GrpcWorkerRegistry.register()`` at all, so it could never catch a
regression in that line. That is exactly the mistake
``test_sdk_register_worker_http_kind.py`` (executor side) warns about in its
own docstring: "the one path actually used in production was the one path
with no test." The gRPC direction repeated it. Fixed to call the real
``register()`` (through a fake gRPC stub) and assert on the *captured*
outbound request — the same shape the HTTP sibling below always used.
"""
import json
import os
import sys

import pytest

# ``workflow_proto`` lives at apps/backend/ai-workflow-management (5 dirs up from
# this test file). The generated stubs live under workflow/proto/generated and
# can be loaded directly with protobuf alone (no grpc) as a fallback.
_AIWM = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
)
_GENERATED = os.path.join(_AIWM, "workflow", "proto", "generated")
for _p in (_AIWM, _GENERATED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration


@pytest.mark.asyncio
async def test_grpc_register_forwards_kind():
    """Calls the REAL ``GrpcWorkerRegistry.register()`` and asserts on the
    request it actually builds and sends — not a hand-built stand-in.

    Proof this is not theater (see PR review): temporarily deleting the
    ``kind=`` line from ``grpc_worker_registry.py``'s ``register()`` makes
    this test fail (the captured request's ``kind`` comes back ``""``
    instead of ``"read"``, since proto3 defaults an unset string field to
    empty); restoring the line makes it pass again. Verified by hand while
    fixing this test; ``grpc_worker_registry.py`` itself is unchanged.
    """
    pytest.importorskip("grpc", reason="grpc not installed")
    from worker_sdk.layer4_frameworks.providers.registry import (
        grpc_worker_registry as mod,
    )

    class FakeResponse:
        worker_id = "w1"

    class FakeStub:
        def __init__(self):
            self.captured = None

        async def Register(self, request, timeout=30.0):
            self.captured = request
            return FakeResponse()

    registry = mod.GrpcWorkerRegistry(grpc_target="localhost:1234")
    fake_stub = FakeStub()
    registry._stub = lambda: fake_stub  # bypass real channel/stub creation — no network needed

    reg = WorkerRegistration(
        worker_type="demo", version="1", endpoint="http://x", kind="read"
    )
    worker_id = await registry.register(reg)

    assert worker_id == "w1"
    assert fake_stub.captured is not None, "register() never called the stub — nothing was sent"
    # Wire-compat proof: the protobuf field carries the plain string "read",
    # not the enum repr "NodeKind.READ" -- assert on the type too, not just
    # string equality (NodeKind.READ == "read" would pass even if the field
    # somehow held the enum member instead of the plain str proto stores).
    assert fake_stub.captured.kind == "read"
    assert type(fake_stub.captured.kind) is str


def test_grpc_protobuf_field_is_plain_string_without_grpc_installed():
    """Lower-level wire-compat proof that does not require the `grpc` pip
    package (only `google.protobuf`) -- so it runs even in environments where
    the test above is skipped for lack of `grpc`. Imports the generated
    message class directly from `workflow.v1` (bypassing `workflow_proto.v1`,
    whose package __init__ eagerly imports the `grpc`-dependent `*_pb2_grpc.py`
    stubs too).
    """
    from workflow.v1 import worker_registry_pb2 as pb  # noqa: no `grpc` import needed
    from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind

    request = pb.RegisterWorkerRequest(
        worker_type="demo", version="1", endpoint="http://x", kind=NodeKind.READ,
    )
    assert request.kind == "read"
    assert type(request.kind) is str


@pytest.mark.asyncio
async def test_http_payload_includes_kind():
    from worker_sdk.layer4_frameworks.providers.registry import (
        http_worker_registry as mod,
    )

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"worker_id": "w1"}}

    class FakeClient:
        async def post(self, url, json):
            captured.update(json)
            return FakeResp()

        async def aclose(self):
            pass

    reg = WorkerRegistration(
        worker_type="demo", version="1", endpoint="http://x", kind="read"
    )
    r = mod.HttpWorkerRegistry()
    r._client = FakeClient()
    await r.register(reg)
    assert captured["kind"] == "read"
    # Wire-compat proof: json.dumps the captured payload the way the real
    # httpx client would and assert the rendered JSON carries the plain
    # string "read" -- never the enum repr "NodeKind.READ".
    assert json.dumps({"kind": captured["kind"]}) == '{"kind": "read"}'
    assert type(captured["kind"]) is str
