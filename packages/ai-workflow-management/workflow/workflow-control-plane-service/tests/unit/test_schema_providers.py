"""Unit tests for ``StaticSchemaProvider`` and ``DataEditSchemaProvider``.

Replaces the deleted ``resolve_schema`` precedence tests that used to
live in ``test_node_data_projection.py``.
"""

from __future__ import annotations

import pytest

from app.layer2_application.node_handlers.schema_providers import (
    ApprovalFlowSchemaProvider,
    DataEditSchemaProvider,
    StaticSchemaProvider,
    _hash_schema,
)
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _Node:
    def __init__(self, config=None, node_id="n1", definition_snapshot=None):
        self.config = config or {}
        self.definition_snapshot = definition_snapshot
        self.id = node_id

        class _T:
            value = "DATA_EDIT"
        self.node_type = _T()
        self.name = "Edit"


class _Task:
    def __init__(self, output_metadata=None):
        self.output_metadata = output_metadata


class _View:
    def __init__(self, variables):
        self.variables = dict(variables)


class _Ctx:
    def __init__(self, variables):
        self.variables = dict(variables)


class _Run:
    def __init__(self, variables):
        self.context = _Ctx(variables)


def _file_ref_with_columns(columns):
    return {
        "__file_ref": True,
        "file_id": "",
        "artifact_id": "__root__",
        "artifacts": [{
            "artifact_id": "table_data",
            "name": "table_data",
            "file_id": "f-table",
            "columns": list(columns),
            "artifact_type": "array_field",
        }],
    }


# ---------------------------------------------------------------------------
# StaticSchemaProvider precedence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_provider_prefers_runtime_task_metadata():
    provider = StaticSchemaProvider()
    task = _Task(output_metadata={"input_schema": {"fields": [{"key": "a"}]}})
    node = _Node(config={"input_schema": {"fields": [{"key": "b"}]}})

    schema, version, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    assert source == "runtime"
    assert schema == {"fields": [{"key": "a"}]}
    assert version.startswith("sha256:")


@pytest.mark.asyncio
async def test_static_provider_falls_back_to_node_definition_template():
    provider = StaticSchemaProvider()
    task = _Task(output_metadata={})
    node = _Node(
        config={"input_schema": {"fields": [{"key": "c"}]}},
        definition_snapshot={"input_schema": {"fields": [{"key": "b"}]}},
    )

    schema, _, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    assert source == "design_time"
    assert schema == {"fields": [{"key": "b"}]}


@pytest.mark.asyncio
async def test_static_provider_falls_back_to_raw_config():
    provider = StaticSchemaProvider()
    task = _Task(output_metadata={})
    node = _Node(config={"input_schema": {"fields": [{"key": "c"}]}})

    schema, _, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    assert source == "design_time"
    assert schema == {"fields": [{"key": "c"}]}


@pytest.mark.asyncio
async def test_static_provider_treats_json_string_as_absent():
    # NodeSpec Phase 4: stored string schemas were parsed once by the
    # migration; a string here is a sender bug — logged, schema empty.
    provider = StaticSchemaProvider()
    task = _Task(output_metadata={})
    node = _Node(config={"input_schema": '{"fields":[{"key":"x"}]}'})

    schema, version, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    assert source == "design_time"
    assert schema == []
    assert version == "v0"


@pytest.mark.asyncio
async def test_static_provider_returns_empty_defaults():
    provider = StaticSchemaProvider()
    task = _Task(output_metadata={})
    node = _Node(config={})

    schema, version, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    assert schema == []
    assert version == "v0"
    assert source == "design_time"


@pytest.mark.asyncio
async def test_static_provider_resolves_design_time_expressions():
    """Design-time ``{{…}}`` must resolve through the *same* resolver the
    PENDING handler uses at dispatch — otherwise a design-time GET /schema
    leaks raw templates while a dispatched node serves resolved values.
    """
    resolver = VariableResolver([TraversalResolverStrategy()])
    provider = StaticSchemaProvider(resolver)
    node = _Node(config={
        "input_schema": [{"key": "name", "default": "{{$upstream.name}}"}],
    })
    task = _Task(output_metadata={})
    run = _Run({"upstream": {"name": "Acme"}})

    schema, version, source = await provider.resolve(
        node=node, task=task, run=run, view=None,
    )
    assert source == "design_time"
    assert schema == [{"key": "name", "default": "Acme"}]
    # The version (ETag) hashes the *resolved* schema the FE actually receives.
    assert version == _hash_schema([{"key": "name", "default": "Acme"}])


@pytest.mark.asyncio
async def test_static_provider_design_time_matches_resolver_routine():
    """Provider output is byte-identical to the shared ``resolve_schema``
    routine the handler calls — proving the two paths share one code path.
    """
    resolver = VariableResolver([TraversalResolverStrategy()])
    provider = StaticSchemaProvider(resolver)
    raw = [
        {"key": "greeting", "default": "Hi {{$upstream.name}}"},
        {"key": "nested", "fieldConfig": {"label": "{{$upstream.name}}"}},
    ]
    node = _Node(config={"input_schema": raw})
    run = _Run({"upstream": {"name": "Acme"}})

    schema, _, _ = await provider.resolve(
        node=node, task=_Task(output_metadata={}), run=run, view=None,
    )
    assert schema == await resolver.resolve_schema(raw, run.context.variables)


@pytest.mark.asyncio
async def test_static_provider_design_time_unchanged_without_resolver():
    """No resolver wired → raw templates pass through unchanged (the
    historical default), keeping bare-provider construction safe.
    """
    provider = StaticSchemaProvider()
    node = _Node(config={
        "input_schema": [{"key": "name", "default": "{{$upstream.name}}"}],
    })
    run = _Run({"upstream": {"name": "Acme"}})

    schema, _, source = await provider.resolve(
        node=node, task=_Task(output_metadata={}), run=run, view=None,
    )
    assert source == "design_time"
    assert schema == [{"key": "name", "default": "{{$upstream.name}}"}]


@pytest.mark.asyncio
async def test_static_provider_runtime_branch_not_re_resolved():
    """The runtime branch is returned verbatim — the handler already resolved
    it at dispatch, so the provider must not touch (or double-resolve) it.
    """
    resolver = VariableResolver([TraversalResolverStrategy()])
    provider = StaticSchemaProvider(resolver)
    runtime = {"fields": [{"key": "a", "default": "{{$upstream.name}}"}]}
    task = _Task(output_metadata={"input_schema": runtime})
    run = _Run({"upstream": {"name": "Acme"}})

    schema, _, source = await provider.resolve(
        node=_Node(), task=task, run=run, view=None,
    )
    assert source == "runtime"
    assert schema == runtime  # untouched


@pytest.mark.asyncio
async def test_static_provider_version_is_deterministic_and_content_sensitive():
    provider = StaticSchemaProvider()
    node = _Node()
    t1 = _Task(output_metadata={"input_schema": {"fields": [{"key": "a"}]}})
    t2 = _Task(output_metadata={"input_schema": {"fields": [{"key": "a"}]}})
    t3 = _Task(output_metadata={"input_schema": {"fields": [{"key": "b"}]}})

    v1 = (await provider.resolve(node=node, task=t1, run=None, view=None))[1]
    v2 = (await provider.resolve(node=node, task=t2, run=None, view=None))[1]
    v3 = (await provider.resolve(node=node, task=t3, run=None, view=None))[1]

    assert v1 == v2
    assert v1 != v3


# ---------------------------------------------------------------------------
# DataEditSchemaProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_data_edit_provider_synthesizes_from_view_columns():
    provider = DataEditSchemaProvider()
    node = _Node()
    task = _Task(output_metadata={"input_schema": [{"key": "stale"}]})
    view = _View({"n1": _file_ref_with_columns(["sku", "qty"])})

    schema, version, source = await provider.resolve(
        node=node, task=task, run=None, view=view,
    )
    assert source == "runtime"
    assert version.startswith("sha256:")
    # auto-form table → row → cells; columns live two levels deep
    cells = schema[0]["fields"][0]["fields"]
    assert [c.get("key") for c in cells] == ["sku", "qty"]


@pytest.mark.asyncio
async def test_data_edit_provider_view_columns_change_bumps_version():
    provider = DataEditSchemaProvider()
    node = _Node()
    task = _Task(output_metadata={})

    view_a = _View({"n1": _file_ref_with_columns(["sku", "qty"])})
    view_b = _View({"n1": _file_ref_with_columns(["sku", "qty", "region"])})

    v_a = (await provider.resolve(node=node, task=task, run=None, view=view_a))[1]
    v_b = (await provider.resolve(node=node, task=task, run=None, view=view_b))[1]

    assert v_a != v_b


@pytest.mark.asyncio
async def test_data_edit_provider_carries_render_config_from_task_metadata():
    """Buttons/settings/readonly stamped at dispatch survive GET /schema."""
    provider = DataEditSchemaProvider()
    node = _Node()
    actions = [{"label": "Approve", "id": "approve"}]
    settings = [{"key": "note", "fieldConfig": {}}]
    task = _Task(output_metadata={
        "readonly": True,
        "custom_data_update": True,
        "actions": actions,
        "settings": settings,
        "instruction": "Review rows",
    })
    view = _View({"n1": _file_ref_with_columns(["sku", "qty"])})

    schema, _, source = await provider.resolve(
        node=node, task=task, run=None, view=view,
    )
    assert source == "runtime"
    table = schema[0]
    assert table["key"] == "__table_data"  # custom_data_update prefix
    wp = table["fieldConfig"]["wrapperProps"]
    assert wp["readonly"] is True
    assert wp["actions"] == actions
    assert wp["label"] == "Review rows"
    assert settings[0] in table["fields"]


@pytest.mark.asyncio
async def test_data_edit_provider_carries_selection_mode_from_task_metadata():
    """SA-1239: the selectionMode stamped at dispatch survives GET /schema, so
    the runtime schema matches the dispatch schema at the actual integration
    point production uses (not just in the builder function in isolation)."""
    provider = DataEditSchemaProvider()
    view = _View({"n1": _file_ref_with_columns(["sku", "qty"])})

    for stamped, expected in (("single", "single"), ("multiple", "multiple")):
        task = _Task(output_metadata={"selectionMode": stamped})
        schema, _, _ = await provider.resolve(
            node=_Node(), task=task, run=None, view=view,
        )
        wp = schema[0]["fieldConfig"]["wrapperProps"]
        assert wp.get("selectionMode") == expected


@pytest.mark.asyncio
async def test_data_edit_provider_none_selection_mode_omits_prop():
    """A "none" designer choice stamped at dispatch yields no selectionMode at
    runtime either — even when the task is readonly (none overrides the default)."""
    provider = DataEditSchemaProvider()
    view = _View({"n1": _file_ref_with_columns(["sku", "qty"])})
    task = _Task(output_metadata={"selectionMode": "none", "readonly": True})

    schema, _, _ = await provider.resolve(
        node=_Node(), task=task, run=None, view=view,
    )
    assert "selectionMode" not in schema[0]["fieldConfig"]["wrapperProps"]


@pytest.mark.asyncio
async def test_data_edit_provider_selection_mode_falls_back_to_node_config():
    """Pre-fix tasks carry no stamped selectionMode → the provider falls back to
    node.config so already-dispatched approval tables keep resolving correctly."""
    provider = DataEditSchemaProvider()
    view = _View({"n1": _file_ref_with_columns(["sku", "qty"])})
    task = _Task(output_metadata={})  # no selectionMode key (pre-fix task)
    node = _Node(config={"selectionMode": "multiple"})

    schema, _, _ = await provider.resolve(
        node=node, task=task, run=None, view=view,
    )
    wp = schema[0]["fieldConfig"]["wrapperProps"]
    assert wp.get("selectionMode") == "multiple"


@pytest.mark.asyncio
async def test_data_edit_provider_falls_back_to_static_when_view_is_none():
    provider = DataEditSchemaProvider()
    task = _Task(output_metadata={"input_schema": [{"key": "cached"}]})
    node = _Node()

    schema, _, source = await provider.resolve(
        node=node, task=task, run=None, view=None,
    )
    # Task metadata wins via the inherited StaticSchemaProvider precedence
    assert source == "runtime"
    assert schema == [{"key": "cached"}]


@pytest.mark.asyncio
async def test_data_edit_provider_falls_back_when_view_has_no_columns():
    provider = DataEditSchemaProvider()
    node = _Node(definition_snapshot={"input_schema": [{"key": "fb"}]})
    task = _Task(output_metadata={})
    view = _View({})  # no entry for n1 → falls through to static precedence

    _, _, source = await provider.resolve(
        node=node, task=task, run=None, view=view,
    )
    assert source == "design_time"


# ---------------------------------------------------------------------------
# _hash_schema
# ---------------------------------------------------------------------------

def test_hash_schema_returns_v0_for_empty():
    assert _hash_schema([]) == "v0"
    assert _hash_schema({}) == "v0"
    assert _hash_schema(None) == "v0"


def test_hash_schema_is_content_addressable():
    a = {"fields": [{"key": "x"}]}
    b = {"fields": [{"key": "x"}]}
    c = {"fields": [{"key": "y"}]}
    assert _hash_schema(a) == _hash_schema(b)
    assert _hash_schema(a) != _hash_schema(c)
    assert _hash_schema(a).startswith("sha256:")


@pytest.mark.asyncio
async def test_approval_flow_provider_surfaces_graph_from_view():
    node = _Node(node_id="appr")
    graph = {"nodes": [{"id": "s1"}], "edges": [{"source": "s1", "target": "s2"}]}
    view = _View({"appr": {"output": {**graph, "change_request_id": "CR1"}}})
    schema, _, source = await ApprovalFlowSchemaProvider().resolve(
        node=node, task=None, run=_Run({}), view=view,
    )
    assert source == "runtime"
    assert schema == graph  # change_request_id dropped, nodes/edges surfaced


@pytest.mark.asyncio
async def test_approval_flow_provider_falls_back_to_run_context():
    node = _Node(node_id="appr")
    graph = {"nodes": [{"id": "s1"}], "edges": []}
    run = _Run({"appr": {"output": graph}})
    schema, _, source = await ApprovalFlowSchemaProvider().resolve(
        node=node, task=None, run=run, view=None,
    )
    assert source == "runtime"
    assert schema == graph


@pytest.mark.asyncio
async def test_approval_flow_provider_empty_before_run():
    schema, version, source = await ApprovalFlowSchemaProvider().resolve(
        node=_Node(node_id="appr"), task=None, run=_Run({}), view=None,
    )
    assert (schema, version, source) == ([], "v0", "design_time")
