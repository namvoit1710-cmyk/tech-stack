"""Tests for GetVariableSuggestionsUseCase (Unit F clean-break shape).

``VariablePath`` now carries ``{path, kind, output_type, sample?}`` — the
legacy ``type``/``label`` fields are gone. The walker recurses through nested
schema ``fields[]`` (depth cap 10), auto-expands file-ref scalar fields into
four sub-paths, and emits five ``kind`` values:
``scalar`` / ``file_ref`` / ``artifact`` / ``artifact_row`` / ``artifact_column``.
"""

import pytest
from unittest.mock import AsyncMock

from app.layer1_domain.entities.data_reference import (
    FILE_REF_MARKER, add_artifact, build_file_ref,
)
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer2_application.features.get_variable_suggestions.use_cases.get_variable_suggestions_use_case import (
    GetVariableSuggestionsCommand,
    GetVariableSuggestionsUseCase,
    MAX_SCHEMA_DEPTH,
    VariablePath,
    _artifacts_registry_paths,
    _collect_upstream_node_ids,
    _finalize_path,
    _walk_schema_tree,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from tests.conftest import DummyLogger


def _build_uc(file_client=None):
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
            file_service_client=file_client, logger=logger,
        ),
        ArtifactsScopeProvider(
            run_repo=run_repo, workflow_repo=workflow_repo,
            file_service_client=file_client, logger=logger,
        ),
    ]
    uc = GetVariableSuggestionsUseCase(
        logger=logger,
        run_repo=run_repo,
        workflow_repo=workflow_repo,
        scope_providers=providers,
    )
    return uc, run_repo, workflow_repo


def _scope(result, scope_type, scope_id=None):
    """Find a scope in the result by type (and optional id)."""
    for s in result.scopes:
        if s.scope_type == scope_type and (scope_id is None or s.scope_id == scope_id):
            return s
    return None


def _node_scopes(result):
    return [s for s in result.scopes if s.scope_type == "node"]


def _make_workflow():
    """A → B → C (linear DAG)."""
    return Workflow(
        id="wf-1", name="Test",
        nodes=[
            WorkflowNode(id="nA", name="NodeA", node_type=NodeType.TASK),
            WorkflowNode(id="nB", name="NodeB", node_type=NodeType.TASK),
            WorkflowNode(id="nC", name="NodeC", node_type=NodeType.TASK),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="nA", target_node_id="nB"),
            WorkflowEdge(id="e2", source_node_id="nB", target_node_id="nC"),
        ],
    )


# ---------------------------------------------------------------------------
# DAG helpers (unchanged from pre-F1)
# ---------------------------------------------------------------------------

class TestCollectUpstream:
    def test_linear_dag(self):
        wf = _make_workflow()
        assert set(_collect_upstream_node_ids(wf, "nC")) == {"nA", "nB"}

    def test_direct_parent_only(self):
        wf = _make_workflow()
        assert set(_collect_upstream_node_ids(wf, "nB")) == {"nA"}

    def test_no_upstream(self):
        wf = _make_workflow()
        assert _collect_upstream_node_ids(wf, "nA") == []


# ---------------------------------------------------------------------------
# _walk_schema_tree — recursion + kinds
# ---------------------------------------------------------------------------

class TestWalkSchemaTree:
    def test_empty_schema(self):
        assert _walk_schema_tree([]) == []

    def test_flat_primitive_fields(self):
        schema = [
            {"key": "name", "outputType": "string", "fieldConfig": {}},
            {"key": "age", "outputType": "number", "fieldConfig": {}},
        ]
        paths = _walk_schema_tree(schema)
        assert [p.path for p in paths] == ["name", "age"]
        assert all(p.kind == "scalar" for p in paths)
        assert paths[0].output_type == "string"
        assert paths[1].output_type == "number"

    def test_nested_object_recurses(self):
        schema = [{
            "key": "address",
            "outputType": "object",
            "fieldConfig": {"fieldWrapper": "DivWrapper"},
            "fields": [
                {"key": "line1", "outputType": "string", "fieldConfig": {}},
                {"key": "city", "outputType": "string", "fieldConfig": {}},
            ],
        }]
        paths = _walk_schema_tree(schema)
        path_strs = [p.path for p in paths]
        assert "address" in path_strs       # parent as scalar/object
        assert "address.line1" in path_strs
        assert "address.city" in path_strs

    def test_file_ref_scalar_auto_expands(self):
        schema = [{
            "key": "evidence",
            "outputType": "object",
            "fieldConfig": {"fieldControl": "UploadControl"},
        }]
        paths = _walk_schema_tree(schema)
        path_strs = [p.path for p in paths]
        assert "evidence" in path_strs
        assert "evidence.file_id" in path_strs
        assert "evidence.filename" in path_strs
        assert "evidence.content_type" in path_strs
        assert "evidence.size_bytes" in path_strs
        # Main entry is kind=file_ref; sub-fields are kind=scalar.
        root = next(p for p in paths if p.path == "evidence")
        assert root.kind == "file_ref"
        sub = next(p for p in paths if p.path == "evidence.file_id")
        assert sub.kind == "scalar"
        assert sub.output_type == "string"

    def test_artifact_bound_emits_three_kinds(self):
        schema = [{
            "key": "orders",
            "outputType": "array",
            "fieldConfig": {
                "fieldWrapper": "TableWrapper",
                "wrapperProps": {"artifact_binding": "orders"},
            },
            "fields": [
                {"key": "sku", "outputType": "string", "fieldConfig": {}},
                {"key": "qty", "outputType": "number", "fieldConfig": {}},
            ],
        }]
        paths = _walk_schema_tree(schema)
        by_path = {p.path: p for p in paths}
        assert "orders" in by_path and by_path["orders"].kind == "artifact"
        assert "orders[*]" in by_path and by_path["orders[*]"].kind == "artifact_row"
        assert "orders[*].sku" in by_path
        assert by_path["orders[*].sku"].kind == "artifact_column"
        assert by_path["orders[*].sku"].output_type == "string"
        assert by_path["orders[*].qty"].kind == "artifact_column"

    def test_nested_table_recurses_with_dotted_row_path(self):
        schema = [{
            "key": "orders", "outputType": "array",
            "fieldConfig": {
                "fieldWrapper": "TableWrapper",
                "wrapperProps": {"artifact_binding": "orders"},
            },
            "fields": [
                {"key": "sku", "outputType": "string", "fieldConfig": {}},
                {
                    "key": "line_items", "outputType": "array",
                    "fieldConfig": {
                        "fieldWrapper": "TableWrapper",
                        "wrapperProps": {"artifact_binding": "orders.line_items"},
                    },
                    "fields": [
                        {"key": "part_no", "outputType": "string", "fieldConfig": {}},
                        {"key": "price", "outputType": "number", "fieldConfig": {}},
                    ],
                },
            ],
        }]
        paths = _walk_schema_tree(schema)
        path_strs = {p.path for p in paths}
        # Parent row-column variants.
        assert "orders[*].sku" in path_strs
        # Nested artifact path.
        assert "orders[*].line_items" in path_strs
        assert "orders[*].line_items[*]" in path_strs
        assert "orders[*].line_items[*].part_no" in path_strs
        assert "orders[*].line_items[*].price" in path_strs

    def test_file_ref_inside_artifact_row_auto_expands(self):
        schema = [{
            "key": "documents", "outputType": "array",
            "fieldConfig": {
                "fieldWrapper": "TableWrapper",
                "wrapperProps": {"artifact_binding": "documents"},
            },
            "fields": [
                {
                    "key": "attachment", "outputType": "object",
                    "fieldConfig": {"fieldControl": "UploadControl"},
                },
            ],
        }]
        paths = _walk_schema_tree(schema)
        by_path = {p.path: p for p in paths}
        assert by_path["documents[*].attachment"].kind == "file_ref"
        assert "documents[*].attachment.file_id" in by_path
        assert by_path["documents[*].attachment.file_id"].kind == "scalar"

    def test_depth_cap_stops_recursion(self):
        """Schema deeper than MAX_SCHEMA_DEPTH is truncated, not crashed."""
        # Build a chain of nested objects MAX+5 levels deep.
        schema = [{"key": "root", "outputType": "object",
                   "fieldConfig": {"fieldWrapper": "DivWrapper"}, "fields": []}]
        cursor = schema[0]
        for i in range(MAX_SCHEMA_DEPTH + 5):
            child = {
                "key": f"level_{i}",
                "outputType": "object",
                "fieldConfig": {"fieldWrapper": "DivWrapper"},
                "fields": [],
            }
            cursor["fields"].append(child)
            cursor = child
        # Does not throw; the last few levels are silently dropped.
        paths = _walk_schema_tree(schema)
        # Assert: no path has more dots than MAX_SCHEMA_DEPTH.
        max_dots = max(p.path.count(".") for p in paths)
        assert max_dots <= MAX_SCHEMA_DEPTH

    def test_sample_attached_from_runtime_samples(self):
        schema = [
            {"key": "name", "outputType": "string", "fieldConfig": {}},
        ]
        paths = _walk_schema_tree(schema, samples={"name": "Acme"})
        assert paths[0].sample == "Acme"


# ---------------------------------------------------------------------------
# _artifacts_registry_paths — metadata-object shape
# ---------------------------------------------------------------------------

class TestArtifactsRegistryPaths:
    def test_emits_file_ref_and_sub_fields_per_entry(self):
        registry = {
            "invoice": {
                "file_id": "f_1", "filename": "i.pdf",
                "content_type": "application/pdf", "size_bytes": 100,
                "produced_by_node": "n", "produced_by_task": "t",
                "registered_at": "z",
            },
        }
        paths = _artifacts_registry_paths(registry)
        by_path = {p.path: p for p in paths}
        assert by_path["invoice"].kind == "file_ref"
        assert by_path["invoice.file_id"].kind == "scalar"
        assert by_path["invoice.filename"].kind == "scalar"
        assert by_path["invoice.content_type"].kind == "scalar"
        assert by_path["invoice.size_bytes"].kind == "scalar"
        # Sample on sub-field should come from the registry entry.
        assert by_path["invoice.file_id"].sample == "f_1"

    def test_filters_legacy_bare_string_entries(self):
        registry = {
            "legacy": "f_bare",  # legacy form; must be skipped.
            "good": {"file_id": "f_ok", "filename": "x", "content_type": "text/csv",
                     "size_bytes": 0, "produced_by_node": "n",
                     "produced_by_task": "t", "registered_at": "z"},
        }
        paths = _artifacts_registry_paths(registry)
        path_strs = {p.path for p in paths}
        assert "good" in path_strs
        assert "legacy" not in path_strs


# ---------------------------------------------------------------------------
# Use case — end-to-end behaviour on the new shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_not_found():
    uc, *_ = _build_uc()
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="x", node_id="y"))
    assert r.error_code == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_returns_workflow_and_run_info():
    uc, run_repo, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nA"))
    assert r.workflow and r.workflow.id == "wf-1"
    assert r.run and r.run.id == "r1"


@pytest.mark.asyncio
async def test_upstream_sources_exclude_target():
    uc, run_repo, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nC"))
    labels = {s.label for s in _node_scopes(r)}
    assert "NodeA" in labels and "NodeB" in labels
    assert "NodeC" not in labels


@pytest.mark.asyncio
async def test_input_source_schema_derived():
    uc, run_repo, wf_repo = _build_uc()
    wf = _make_workflow()
    wf.input_schema = [
        {"key": "customer_id", "outputType": "string", "fieldConfig": {}},
    ]
    wf_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {"customer_id": "C-001"}
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nA"))
    inp = _scope(r, "input")
    assert inp is not None
    assert inp.expression_prefix == "{{$input"
    paths = {p.path: p for p in inp.paths}
    assert "customer_id" in paths
    assert paths["customer_id"].kind == "scalar"
    assert paths["customer_id"].sample == "C-001"


@pytest.mark.asyncio
async def test_file_ref_context_surfaces_preview_and_file_id():
    mock_client = AsyncMock()
    mock_client.query_file_data.return_value = {
        "file_id": "f-1", "total_matches": 2,
        "data": [
            {"name": "A", "amount": "1", "_row_index": "0"},
            {"name": "B", "amount": "2", "_row_index": "1"},
        ],
    }
    uc, run_repo, wf_repo = _build_uc(file_client=mock_client)
    wf_repo.save(_make_workflow())
    ref = build_file_ref(
        file_id="f-1", columns=["name", "amount"],
    )
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run.context.variables["nA"] = ref
    run_repo.save(run)

    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nB"))
    node_a = next(s for s in _node_scopes(r) if s.label == "NodeA")
    assert node_a.has_file_data is True
    assert node_a.file_id == "f-1"
    assert len(node_a.preview_rows) == 2


@pytest.mark.asyncio
async def test_node_scope_backfills_scalar_samples_from_wire_thinned_ref():
    """Regression: a submitted INPUT node reaches the read side wire-thinned —
    ``NodeOutputProduced`` strips the file_ref's ``extracted`` cache to stay
    under broker size limits. With no ``extracted``, ``_build_source_for_node``
    sets every scalar path's ``sample`` to None and the FE shows no preview
    value. ``_enrich`` must backfill scalar samples from the root preview row it
    already fetches (mirrors the ``$input`` scope's ``run.input_data`` fallback).
    """
    mock_client = AsyncMock()
    mock_client.query_file_data.return_value = {
        "file_id": "f-root", "total_matches": 1,
        "data": [{
            "__row_id": "r-1", "triggerId": "ds",
            "description": "dsd", "objectType": "1", "_row_index": "0",
        }],
    }
    uc, run_repo, wf_repo = _build_uc(file_client=mock_client)
    wf_repo.save(_make_workflow())
    # Wire-thinned root ref: real file_id + columns, but NO ``extracted`` cache
    # (exactly what survives ``wire_thin_file_ref`` on NodeOutputProduced).
    ref = build_file_ref(
        file_id="f-root",
        columns=["__row_id", "triggerId", "description", "objectType"],
    )
    assert not ref.get("extracted")
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run.context.variables["nA"] = ref
    run_repo.save(run)

    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nB"))
    node_a = next(s for s in _node_scopes(r) if s.label == "NodeA")
    by_path = {p.path: p for p in node_a.paths}
    assert by_path["triggerId"].sample == "ds"
    assert by_path["description"].sample == "dsd"
    assert by_path["objectType"].sample == "1"
    # The synthetic row-id column is not surfaced as a referenceable path.
    assert "__row_id" not in by_path


@pytest.mark.asyncio
async def test_node_scope_surfaces_file_columns_when_schema_and_ctx_columns_empty():
    """A wire-thinned ref with an EMPTY ``columns`` list and a node with no
    ``output_schema`` leaves ``_build_source_for_node`` with nothing to emit a
    path from. The file itself still has rows by ``file_id`` — ``_enrich`` must
    surface those columns as scalar paths sampled from the first row, instead
    of dropping them (FE would otherwise see no path and no sample).
    """
    mock_client = AsyncMock()
    mock_client.query_file_data.return_value = {
        "file_id": "f-root", "total_matches": 1,
        "data": [{
            "__row_id": "r-1", "customer_id": "C-001",
            "amount": "42", "_row_index": "0",
        }],
    }
    uc, run_repo, wf_repo = _build_uc(file_client=mock_client)
    wf_repo.save(_make_workflow())
    # Real file_id, but NO ``extracted`` cache and NO ``columns`` list.
    ref = build_file_ref(file_id="f-root", columns=[])
    assert not ref.get("extracted")
    assert not ref.get("columns")
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run.context.variables["nA"] = ref
    run_repo.save(run)

    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nB"))
    node_a = next(s for s in _node_scopes(r) if s.label == "NodeA")
    by_path = {p.path: p for p in node_a.paths}
    assert by_path["customer_id"].sample == "C-001"
    assert by_path["amount"].sample == "42"
    # Synthetic row-id column is still not surfaced as a referenceable path.
    assert "__row_id" not in by_path


@pytest.mark.asyncio
async def test_artifacts_registry_metadata_object_shape():
    uc, run_repo, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run.context.variables["artifacts"] = {
        "report.csv": {
            "file_id": "f-abc", "filename": "report.csv",
            "content_type": "text/csv", "size_bytes": 0,
            "produced_by_node": "producer", "produced_by_task": "t-1",
            "registered_at": "2026-04-16T10:00:00+00:00",
        },
    }
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nA"))
    arts = _scope(r, "artifacts")
    assert arts is not None
    assert arts.expression_prefix == "{{$artifacts"
    paths = {p.path: p for p in arts.paths}
    assert paths["report.csv"].kind == "file_ref"
    assert paths["report.csv.file_id"].kind == "scalar"
    assert paths["report.csv.file_id"].sample == "f-abc"


@pytest.mark.asyncio
async def test_no_artifacts_emits_empty_scope():
    """Empty registry → ``artifacts`` scope still emitted as a default card
    with no paths, so the picker shows it as a discoverability hint."""
    uc, run_repo, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nA"))
    arts = _scope(r, "artifacts")
    assert arts is not None
    assert arts.expression_prefix == "{{$artifacts"
    assert arts.paths == []


@pytest.mark.asyncio
async def test_inline_dict_without_schema_emits_paths_from_runtime_samples():
    """When an upstream node has no declared output_schema and its context
    value is a plain dict (e.g., legacy inline output, or HTTP-worker JSON
    response that wasn't wrapped to a file_ref), the use case must still
    surface a {{$<node_id>.<key>}} path per top-level key so the FE has
    something to bind to. Types are inferred from the runtime value."""
    uc, run_repo, wf_repo = _build_uc()
    wf_repo.save(_make_workflow())
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run.context.variables["nA"] = {
        "status_code": 200,
        "is_success": True,
        "response_body": "ok",
        "response_headers": {"content-type": "application/json"},
        "tags": ["a", "b"],
    }
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nB"))
    nA = next(s for s in _node_scopes(r) if s.label == "NodeA")
    by_path = {p.path: p for p in nA.paths}

    assert by_path["status_code"].kind == "scalar"
    assert by_path["status_code"].output_type == "number"
    assert by_path["status_code"].sample == 200

    assert by_path["is_success"].output_type == "boolean"
    assert by_path["is_success"].sample is True

    assert by_path["response_body"].output_type == "string"
    assert by_path["response_body"].sample == "ok"

    assert by_path["response_headers"].output_type == "object"
    assert by_path["response_headers.content-type"].output_type == "string"

    assert by_path["tags"].output_type == "array"


@pytest.mark.asyncio
async def test_variable_path_shape_carries_new_fields():
    """VariablePath shape: legacy ``type`` is gone; new FE-friendly fields
    ``label`` / ``expandable`` / ``display_path`` are present alongside
    ``columns`` / ``preview_rows``. Note: ``label`` is reused as a NEW field
    (path tail) — semantically distinct from the legacy schema-author label.
    """
    uc, run_repo, wf_repo = _build_uc()
    wf = _make_workflow()
    wf.input_schema = [
        {"key": "customer_id", "outputType": "string", "fieldConfig": {}},
    ]
    wf_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {}
    run_repo.save(run)
    r = await uc.execute(GetVariableSuggestionsCommand(run_id="r1", node_id="nA"))
    import dataclasses as _dc
    inp = _scope(r, "input")
    assert inp is not None and inp.paths
    p = inp.paths[0]
    names = {f.name for f in _dc.fields(p)}
    assert "type" not in names  # legacy field absent
    assert {"path", "kind", "output_type", "sample"}.issubset(names)
    assert {"columns", "preview_rows"}.issubset(names)
    assert {"label", "expandable", "display_path"}.issubset(names)


class TestFinalizePath:
    """``_finalize_path`` derives FE-friendly fields once before serialization."""

    def test_label_is_path_tail(self):
        p = _finalize_path(VariablePath(path="metadata.child_workflow_id"),
                           expression_prefix="{{$abc")
        assert p.label == "child_workflow_id"

    def test_label_for_root_path(self):
        p = _finalize_path(VariablePath(path="orderTable"),
                           expression_prefix="{{$abc")
        assert p.label == "orderTable"

    def test_expandable_only_when_file_ref_with_columns(self):
        scalar = _finalize_path(VariablePath(path="foo", kind="scalar"),
                                expression_prefix="{{$abc")
        empty_ref = _finalize_path(VariablePath(path="foo", kind="file_ref"),
                                   expression_prefix="{{$abc")
        ref_with_cols = _finalize_path(
            VariablePath(path="foo", kind="file_ref", columns=["a", "b"]),
            expression_prefix="{{$abc",
        )
        assert scalar.expandable is False
        assert empty_ref.expandable is False
        assert ref_with_cols.expandable is True

    def test_display_path_format(self):
        p = _finalize_path(VariablePath(path="orderTable"),
                           expression_prefix="{{$uid")
        assert p.display_path == "{{$uid.orderTable}}"

    def test_display_path_with_dotted_path(self):
        p = _finalize_path(VariablePath(path="metadata.type"),
                           expression_prefix="{{$uid")
        assert p.display_path == "{{$uid.metadata.type}}"

    def test_idempotent(self):
        p = VariablePath(path="orderTable", kind="file_ref", columns=["a"])
        _finalize_path(p, expression_prefix="{{$x")
        _finalize_path(p, expression_prefix="{{$x")
        assert p.display_path == "{{$x.orderTable}}"
        assert p.expandable is True
        assert p.label == "orderTable"
