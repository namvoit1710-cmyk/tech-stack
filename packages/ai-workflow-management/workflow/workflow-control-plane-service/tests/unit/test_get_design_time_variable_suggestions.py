"""Tests for GetDesignTimeVariableSuggestionsUseCase.

Workflow-scoped autocomplete: no run is ever seeded — the shared scope
providers are exercised with ``run=None`` / ``variables={}`` and must stay
schema-only (no samples, no file service, no child-run scopes).
"""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects import NodeType
from app.layer2_application.features.get_design_time_variable_suggestions.use_cases.get_design_time_variable_suggestions_use_case import (
    GetDesignTimeVariableSuggestionsCommand,
    GetDesignTimeVariableSuggestionsUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from tests.conftest import DummyLogger


def _build_uc():
    from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
        ArtifactsScopeProvider,
        InputScopeProvider,
        NodeScopeProvider,
        RunScopeProvider,
        VariablesScopeProvider,
        WorkflowScopeProvider,
    )
    run_repo = MemoryRunRepository()
    workflow_repo = MemoryWorkflowRepository()
    logger = DummyLogger()
    providers = [
        RunScopeProvider(),
        WorkflowScopeProvider(),
        VariablesScopeProvider(),
        InputScopeProvider(),
        NodeScopeProvider(
            run_repo=run_repo, workflow_repo=workflow_repo,
            file_service_client=None, logger=logger,
        ),
        ArtifactsScopeProvider(
            run_repo=run_repo, workflow_repo=workflow_repo,
            file_service_client=None, logger=logger,
        ),
    ]
    uc = GetDesignTimeVariableSuggestionsUseCase(
        logger=logger,
        workflow_repo=workflow_repo,
        scope_providers=providers,
    )
    return uc, workflow_repo


def _schema(*keys_and_types):
    return [
        {"key": k, "outputType": t, "fieldConfig": {}}
        for k, t in keys_and_types
    ]


def _make_workflow():
    """n1 → n2 → n3; n1 declares an output schema with nested address."""
    n1_schema = [
        {"key": "address", "outputType": "object", "fieldConfig": {},
         "fields": _schema(("city", "string"), ("zip", "string"))},
        {"key": "name", "outputType": "string", "fieldConfig": {}},
    ]
    return Workflow(
        id="wf-1", name="Test",
        input_schema=_schema(("customer_id", "string")),
        nodes=[
            WorkflowNode(id="n1", name="NodeOne", node_type=NodeType.TASK,
                         config={"output_schema": n1_schema}),
            WorkflowNode(id="n2", name="NodeTwo", node_type=NodeType.TASK),
            WorkflowNode(id="n3", name="NodeThree", node_type=NodeType.TASK,
                         config={"output_schema": _schema(("secret", "string"))}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="n1", target_node_id="n2"),
            WorkflowEdge(id="e2", source_node_id="n2", target_node_id="n3"),
        ],
    )


def _cmd(node_id="n2", q="", limit=50, workflow_id="wf-1"):
    return GetDesignTimeVariableSuggestionsCommand(
        workflow_id=workflow_id, node_id=node_id, q=q, limit=limit,
    )


def _scope_summary(result, scope_type, scope_id=None):
    for s in result.scopes:
        if s.scope_type == scope_type and (scope_id is None or s.scope_id == scope_id):
            return s
    return None


@pytest.mark.asyncio
async def test_unknown_workflow_returns_error_code():
    uc, _ = _build_uc()
    r = await uc.execute(_cmd(workflow_id="ghost"))
    assert r.error_code == "WORKFLOW_NOT_FOUND"
    assert r.matches == []


@pytest.mark.asyncio
async def test_empty_q_returns_all_scopes_and_paths():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q=""))
    assert r.error is None
    scope_types = {s.scope_type for s in r.scopes}
    assert {"run", "workflow", "variables", "input", "node", "artifacts"} <= scope_types
    assert all(m.match_kind == "all" for m in r.matches)
    node_paths = {m.path for m in r.matches if m.scope_type == "node"}
    assert {"address", "address.city", "address.zip", "name"} <= node_paths
    # design time — no samples anywhere, display_path composed by providers
    by_path = {m.path: m for m in r.matches if m.scope_type == "node"}
    assert by_path["address.city"].display_path == "{{$n1.address.city}}"


@pytest.mark.asyncio
async def test_upstream_only_visibility():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q=""))
    node_scope_ids = {s.scope_id for s in r.scopes if s.scope_type == "node"}
    assert node_scope_ids == {"n1"}
    # downstream node's source resolves to nothing — 200 with zero matches
    r2 = await uc.execute(_cmd(node_id="n2", q="$n3."))
    assert r2.error is None
    assert r2.matches == []
    assert r2.total_matches == 0


@pytest.mark.asyncio
async def test_source_prefix_suggests_sources():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q="$in"))
    assert [m.match_kind for m in r.matches] == ["source"]
    assert r.matches[0].scope_type == "input"
    assert r.matches[0].display_path == "{{$input"


@pytest.mark.asyncio
async def test_path_prefix_before_substring():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q="$n1.add"))
    # depth before length: "address" first; then "address.zip" (shorter)
    assert [m.path for m in r.matches] == ["address", "address.zip", "address.city"]
    assert all(m.match_kind == "prefix" for m in r.matches)
    r2 = await uc.execute(_cmd(node_id="n2", q="$n1.ity"))
    assert [m.path for m in r2.matches] == ["address.city"]
    assert r2.matches[0].match_kind == "substring"


@pytest.mark.asyncio
async def test_bare_term_searches_globally():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q="customer"))
    paths = {(m.scope_type, m.path) for m in r.matches}
    assert ("input", "customer_id") in paths


@pytest.mark.asyncio
async def test_unsaved_node_exposes_all_nodes():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="ghost", q=""))
    assert r.error is None
    node_scope_ids = {s.scope_id for s in r.scopes if s.scope_type == "node"}
    assert node_scope_ids == {"n1", "n2", "n3"}


@pytest.mark.asyncio
async def test_limit_truncation_and_counters():
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n2", q="", limit=3))
    assert len(r.matches) == 3
    assert r.truncated is True
    assert r.total_matches > 3
    assert sum(s.returned_count for s in r.scopes) == 3
    assert any(s.truncated and s.returned_count < s.match_count for s in r.scopes)


@pytest.mark.asyncio
async def test_set_variable_declarations_visible_without_run():
    uc, wf_repo = _build_uc()
    wf = _make_workflow()
    wf.nodes.append(WorkflowNode(
        id="nv", name="SetVars", node_type=NodeType.SET_VARIABLE,
        config={"variables": [{"name": "counter", "type": "number"}]},
    ))
    wf_repo.save(wf)
    r = await uc.execute(_cmd(node_id="n2", q="$variables."))
    by_path = {m.path: m for m in r.matches}
    assert "counter" in by_path
    assert by_path["counter"].output_type == "number"


@pytest.mark.asyncio
async def test_set_variable_without_type_visible_without_run():
    # type field has no form default — a variable authored without one must
    # still surface in the picker (regression: type-less entries were dropped).
    uc, wf_repo = _build_uc()
    wf = _make_workflow()
    wf.nodes.append(WorkflowNode(
        id="nv", name="SetVars", node_type=NodeType.SET_VARIABLE,
        config={"variables": [{"name": "counter"}]},
    ))
    wf_repo.save(wf)
    r = await uc.execute(_cmd(node_id="n2", q="$variables."))
    by_path = {m.path: m for m in r.matches}
    assert "counter" in by_path
    assert by_path["counter"].output_type == "string"


@pytest.mark.asyncio
async def test_variable_alias_resolves_to_variables_scope():
    # Picker exposes only $variables; typing the singular $variable. is aliased
    # to it (so a SET_VARIABLE name surfaces either way).
    uc, wf_repo = _build_uc()
    wf = _make_workflow()
    wf.nodes.append(WorkflowNode(
        id="nv", name="SetVars", node_type=NodeType.SET_VARIABLE,
        config={"variables": [{"name": "counter", "type": "number"}]},
    ))
    wf_repo.save(wf)
    r = await uc.execute(_cmd(node_id="n2", q="$variable.cou"))
    assert [m.path for m in r.matches] == ["counter"]
    assert all(m.scope_type == "variables" for m in r.matches)


@pytest.mark.asyncio
async def test_run_none_is_safe_across_all_providers():
    """Full provider list (incl. the child-run code paths) with run=None."""
    uc, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    r = await uc.execute(_cmd(node_id="n3", q=""))
    assert r.error is None
    artifacts = _scope_summary(r, "artifacts")
    assert artifacts is not None and artifacts.match_count == 0
    run_scope = _scope_summary(r, "run")
    assert run_scope is not None and run_scope.match_count > 0
