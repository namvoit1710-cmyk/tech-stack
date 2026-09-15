"""Tests for scope providers (variable-suggestions endpoint).

One test class per provider. Providers are pure async functions of their
inputs (run, workflow, variables, upstream_node_ids); tests assert path
shapes and sample values without any I/O.
"""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
    RunScopeProvider,
    VariablesScopeProvider,
    WorkflowScopeProvider,
)


def _make_run() -> WorkflowRun:
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.started_at = "2026-05-07T10:00:00+00:00"
    return run


def _make_workflow() -> Workflow:
    wf = Workflow(id="wf-1", name="Test", version="1")
    wf.description = "demo"
    wf.status = "DRAFT"
    return wf


@pytest.mark.asyncio
class TestRunScopeProvider:
    async def test_emits_one_scope_with_run_paths(self):
        provider = RunScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        assert len(scopes) == 1
        s = scopes[0]
        assert s.scope_type == "run"
        assert s.scope_id == "run"
        assert s.label == "Run"
        assert s.expression_prefix == "{{$run"
        path_keys = {p.path for p in s.paths}
        assert {"id", "workflow_id", "status", "started_at"}.issubset(path_keys)

    async def test_paths_are_finalized(self):
        provider = RunScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert by_path["id"].label == "id"
        assert by_path["id"].display_path == "{{$run.id}}"
        assert by_path["id"].sample == "run-1"
        assert by_path["id"].expandable is False

    async def test_status_enum_coerced_to_value(self):
        provider = RunScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        # RunStatus.RUNNING.value == "RUNNING"; should be the string, not the enum.
        assert by_path["status"].sample == "RUNNING"

    async def test_missing_attrs_become_none(self):
        provider = RunScopeProvider()
        run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING)
        # No completed_at / parent_run_id set.
        scopes = await provider.build(
            run=run, target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert by_path["completed_at"].sample is None
        assert by_path["parent_run_id"].sample is None


@pytest.mark.asyncio
class TestWorkflowScopeProvider:
    async def test_emits_one_scope_with_workflow_paths(self):
        provider = WorkflowScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        assert len(scopes) == 1
        s = scopes[0]
        assert s.scope_type == "workflow"
        assert s.scope_id == "workflow"
        assert s.label == "Workflow"
        assert s.expression_prefix == "{{$workflow"
        path_keys = {p.path for p in s.paths}
        assert {"id", "name", "version", "description"}.issubset(path_keys)

    async def test_paths_are_finalized(self):
        provider = WorkflowScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert by_path["name"].sample == "Test"
        assert by_path["name"].display_path == "{{$workflow.name}}"
        assert by_path["description"].sample == "demo"


@pytest.mark.asyncio
class TestVariablesScopeProvider:
    async def test_emits_scope_even_when_empty(self):
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={}, upstream_node_ids=[],
        )
        assert len(scopes) == 1
        s = scopes[0]
        assert s.scope_type == "variables"
        assert s.scope_id == "variables"
        assert s.label == "Variables"
        assert s.expression_prefix == "{{$variables"
        assert s.paths == []

    async def test_paths_match_set_variables(self):
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={"__vars": {"userCount": 42, "label": "active", "isReady": True}},
            upstream_node_ids=[],
        )
        s = scopes[0]
        by_path = {p.path: p for p in s.paths}
        assert set(by_path.keys()) == {"userCount", "label", "isReady"}
        assert by_path["userCount"].sample == 42
        assert by_path["userCount"].output_type == "number"
        assert by_path["userCount"].display_path == "{{$variables.userCount}}"
        assert by_path["label"].output_type == "string"
        assert by_path["isReady"].output_type == "boolean"

    async def test_paths_sorted_by_name(self):
        # Stable order in the picker.
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={"__vars": {"zeta": 1, "alpha": 2, "mu": 3}},
            upstream_node_ids=[],
        )
        names = [p.path for p in scopes[0].paths]
        assert names == ["alpha", "mu", "zeta"]

    async def test_file_ref_vars_surfaces_no_inline_samples(self):
        # No-``extracted`` model: a file-backed ``__vars`` is a pointer-only
        # file_ref carrying no inline scalars, so it contributes no
        # sample-bearing paths. The picker surfaces DECLARED SET_VARIABLE names
        # (none in this workflow) — a file_ref with no declarations yields no
        # paths. (Inline sample values would need a bounded OData fetch, which
        # is a deferred follow-up; the plain-dict path is covered above.) The
        # provider must not crash or leak file_ref bookkeeping keys.
        wrapped = {
            "__file_ref": True,
            "file_id": "abc.csv",
            "row_count": 1,
            "columns": ["userCount", "label"],
        }
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={"__vars": wrapped}, upstream_node_ids=[],
        )
        paths = scopes[0].paths if scopes else []
        names = {p.path for p in paths}
        # No file_ref bookkeeping keys leak; no inline samples surface.
        assert "file_id" not in names and "columns" not in names
        assert names == set()

    async def test_declared_type_wins_over_inferred(self):
        # SET_VARIABLE declares ``userCount: number`` and ``tags: array<string>``.
        # Sample is None for ``tags`` (not yet set) and 42 for ``userCount``.
        # The picker reports the DECLARED type/item_type, not the inferred
        # type from the runtime sample — stable before any run, picks up
        # array<T> shape that inference cannot infer.
        wf = _make_workflow()
        wf.nodes = [
            WorkflowNode(
                id="set-1", node_type=NodeType.SET_VARIABLE,
                config={"variables": [
                    {"name": "userCount", "type": "number"},
                    {"name": "tags", "type": "array", "item_type": "string"},
                ]},
            ),
        ]
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1", target_workflow=wf,
            variables={"__vars": {"userCount": 42}},  # tags not yet written
            upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert set(by_path.keys()) == {"userCount", "tags"}
        assert by_path["userCount"].output_type == "number"
        assert by_path["userCount"].item_type is None
        assert by_path["userCount"].sample == 42
        assert by_path["tags"].output_type == "array"
        assert by_path["tags"].item_type == "string"
        assert by_path["tags"].sample is None  # surfaced before first write

    async def test_declared_without_type_surfaces_name(self):
        # A SET_VARIABLE entry authored without a ``type`` (the field has no
        # form default) must still surface the name. Design-time (no sample)
        # falls back to ``string``; with a runtime sample the type is inferred.
        wf = _make_workflow()
        wf.nodes = [
            WorkflowNode(
                id="set-1", node_type=NodeType.SET_VARIABLE,
                config={"variables": [{"name": "greet"}]},
            ),
        ]
        provider = VariablesScopeProvider()
        # design time: no run values
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1", target_workflow=wf,
            variables={}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert "greet" in by_path
        assert by_path["greet"].output_type == "string"
        # runtime: sample present → inferred type wins over the absent declaration
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1", target_workflow=wf,
            variables={"__vars": {"greet": 42}}, upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert by_path["greet"].output_type == "number"

    async def test_inferred_type_when_no_declaration(self):
        # No SET_VARIABLE node declares ``rogue`` — it was written by some
        # other path (e.g. test fixture). Falls back to inferred type.
        provider = VariablesScopeProvider()
        scopes = await provider.build(
            run=_make_run(), target_node_id="n1",
            target_workflow=_make_workflow(),
            variables={"__vars": {"rogue": [1, 2, 3]}},
            upstream_node_ids=[],
        )
        by_path = {p.path: p for p in scopes[0].paths}
        assert by_path["rogue"].output_type == "array"
        assert by_path["rogue"].item_type is None  # nothing to declare
