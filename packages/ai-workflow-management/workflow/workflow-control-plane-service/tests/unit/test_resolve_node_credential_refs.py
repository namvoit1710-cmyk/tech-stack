"""T3 — resolve node credential_refs against the vault at StartRun (F4/F5/F11/F17).

The one new producer step: collect each node's credential_ref, resolve it via the
vault client, and return a {ref: {type, config}} map to merge into runtime_credentials
(author-supplied entries win on key collision). Downstream encryption + the worker
coalesce are unchanged.
"""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer2_application.features.start_run.use_cases.resolve_node_credential_refs import (
    collect_and_resolve,
)


class FakeVaultClient:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    def put(self, ref: str, entry: dict) -> None:
        self._store[ref] = entry

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        return {**self._store[credential_id], "expires_at": None}


def _wf_with_ref(ref: str = "cred_123") -> Workflow:
    node = WorkflowNode(workflow_id="n1", name="HTTP", worker_type="http_request",
                        config={"credential_ref": ref})
    return Workflow(workflow_id="wf1", name="W", nodes=[node])


@pytest.mark.asyncio
async def test_collects_refs_and_resolves_against_vault():
    client = FakeVaultClient()
    client.put("cred_123", {"type": "api_key", "config": {"header": "X-API-Key", "value": "k"}})
    resolved = await collect_and_resolve(_wf_with_ref(), tenant_id="t1", user_id=None, client=client)
    assert resolved["cred_123"]["type"] == "api_key"
    assert resolved["cred_123"]["config"] == {"header": "X-API-Key", "value": "k"}
    # only {type, config} land in the map — no expires_at leak
    assert set(resolved["cred_123"].keys()) == {"type", "config"}
    # tenant is always the run's tenant (TENANT_BLEED)
    assert client.calls == [("t1", "cred_123", None)]


@pytest.mark.asyncio
async def test_author_runtime_credential_wins_over_ref():
    client = FakeVaultClient()
    client.put("cred_123", {"type": "api_key", "config": {"header": "X-API-Key", "value": "k"}})
    resolved = await collect_and_resolve(_wf_with_ref(), "t1", None, client)
    existing = {"cred_123": {"type": "bearer", "config": {"token": "author"}}}
    merged = {**resolved, **existing}
    assert merged["cred_123"]["config"].get("token") == "author"


@pytest.mark.asyncio
async def test_no_refs_returns_empty_and_makes_no_calls():
    node = WorkflowNode(workflow_id="n1", name="wait", worker_type="wait")
    wf = Workflow(workflow_id="wf1", name="W", nodes=[node])
    client = FakeVaultClient()
    assert await collect_and_resolve(wf, "t1", None, client) == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_dedupes_repeated_refs():
    node_a = WorkflowNode(workflow_id="a", name="A", worker_type="http_request",
                          config={"credential_ref": "cred_x"})
    node_b = WorkflowNode(workflow_id="b", name="B", worker_type="http_request",
                          config={"credential_ref": "cred_x"})
    wf = Workflow(workflow_id="wf1", name="W", nodes=[node_a, node_b])
    client = FakeVaultClient()
    client.put("cred_x", {"type": "bearer", "config": {"token": "t"}})
    resolved = await collect_and_resolve(wf, "t1", None, client)
    assert list(resolved) == ["cred_x"]
    assert len(client.calls) == 1
