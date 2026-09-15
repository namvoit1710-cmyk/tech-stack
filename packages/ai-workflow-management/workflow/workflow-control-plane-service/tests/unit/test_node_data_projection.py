"""Unit tests for node_data_projection helpers."""

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer2_application.read.node_data_projection import (
    build_descriptor_tree,
    build_inline_descriptor,
    collect_file_ids,
    compute_node_state,
    descendant_file_ids,
    find_latest_task_for_node,
    is_pending_node_type,
    synthesize_data_edit_schema,
)


# ---------------------------------------------------------------------------
# build_descriptor_tree
# ---------------------------------------------------------------------------

def _tree_three_level():
    ref = build_file_ref(file_id="f_main", columns=["a", "b"])
    orders = add_artifact(ref, file_id="f_orders", name="orders", artifact_id="orders",
                          artifact_type="array_field")
    line_items = add_artifact(
        orders, file_id="f_li", name="line_items",
        artifact_id="orders.line_items",
        artifact_type="nested_array_field",
        parent_row_key_column="__parent_row_id",
    )
    add_artifact(
        line_items, file_id="f_attrs", name="attrs",
        artifact_id="orders.line_items.attrs",
        artifact_type="nested_array_field",
        parent_row_key_column="__parent_row_id",
    )
    # Set columns on artifacts
    orders["columns"] = ["__row_id", "sku"]
    line_items["columns"] = ["__row_id", "__parent_row_id", "part_no"]
    return ref


def test_build_descriptor_tree_produces_recursive_shape():
    ref = _tree_three_level()
    desc = build_descriptor_tree(ref)

    assert desc is not None
    assert desc["artifact_id"] == "__root__"
    assert desc["file_id"] == "f_main"
    assert desc["parent_row_key_column"] is None
    assert len(desc["children"]) == 1

    orders = desc["children"][0]
    assert orders["artifact_id"] == "orders"
    assert orders["artifact_type"] == "array_field"
    assert orders["columns"] == ["__row_id", "sku"]
    assert len(orders["children"]) == 1

    li = orders["children"][0]
    assert li["artifact_id"] == "orders.line_items"
    assert li["parent_row_key_column"] == "__parent_row_id"
    assert len(li["children"]) == 1

    attrs = li["children"][0]
    assert attrs["artifact_id"] == "orders.line_items.attrs"
    assert attrs["children"] == []


def test_build_descriptor_tree_has_no_row_count_field():
    ref = _tree_three_level()
    desc = build_descriptor_tree(ref)

    def _assert_no_row_count(node):
        assert "row_count" not in node
        for child in node["children"]:
            _assert_no_row_count(child)

    _assert_no_row_count(desc)


def test_build_descriptor_tree_none_for_non_file_ref():
    assert build_descriptor_tree(None) is None
    assert build_descriptor_tree({"not": "a ref"}) is None
    assert build_descriptor_tree("string") is None


# ---------------------------------------------------------------------------
# build_inline_descriptor — SA-1905 C2: file_ref fields nested in inline output
# ---------------------------------------------------------------------------

def test_build_inline_descriptor_pure_inline_keeps_values_in_row():
    desc = build_inline_descriptor({"result": 42, "note": "hi"})
    assert desc is not None
    assert desc["file_id"] == ""
    assert desc["inline"] is True
    assert desc["children"] == []
    assert desc["columns"] == ["result", "note"]
    assert desc["preview_rows"] == [{"result": 42, "note": "hi"}]


def test_build_inline_descriptor_hides_envelope_keys():
    desc = build_inline_descriptor(
        {"result": [1, 2], "output": {"uri": "x"}, "metadata": {"a": 1}}
    )
    assert desc["columns"] == ["result"]
    assert "output" not in desc["preview_rows"][0]
    assert "metadata" not in desc["preview_rows"][0]


def test_build_inline_descriptor_promotes_file_ref_field_to_child():
    # A code node whose large ``result`` array the worker streamed to CSV:
    # the output is ``{result: <file_ref>, logs: [...]}``. ``result`` must
    # become a file-backed CHILD (so the grid renders a sub-table) and must
    # NOT leak the pointer object into the inline row.
    ref = build_file_ref(file_id="f_result", columns=["i", "sku"])
    desc = build_inline_descriptor({"result": ref, "logs": ["done"]})

    # Non-file fields stay inline; the file_ref field is gone from the row.
    assert desc["columns"] == ["logs"]
    assert desc["preview_rows"] == [{"logs": ["done"]}]
    assert "result" not in desc["preview_rows"][0]

    # ``result`` is now a file-backed child keyed by the field name.
    assert len(desc["children"]) == 1
    child = desc["children"][0]
    assert child["artifact_id"] == "result"
    assert child["file_id"] == "f_result"
    assert child["columns"] == ["i", "sku"]


def test_build_inline_descriptor_all_file_ref_fields_empty_row():
    # Output is purely ``{result: <file_ref>}`` — no inline scalars remain.
    ref = build_file_ref(file_id="f_result", columns=["i"])
    desc = build_inline_descriptor({"result": ref})
    assert desc["columns"] == []
    assert desc["preview_rows"] == []
    assert [c["artifact_id"] for c in desc["children"]] == ["result"]


def test_build_inline_descriptor_none_for_non_dict():
    assert build_inline_descriptor([1, 2, 3]) is None
    assert build_inline_descriptor("x") is None


# ---------------------------------------------------------------------------
# descendant_file_ids
# ---------------------------------------------------------------------------

def test_descendant_file_ids_includes_root_and_descendants():
    ref = _tree_three_level()
    ids = descendant_file_ids(ref)
    assert "f_main" in ids
    assert "f_orders" in ids
    assert "f_li" in ids
    assert "f_attrs" in ids
    assert len(ids) == 4


def test_descendant_file_ids_empty_for_non_file_ref():
    assert descendant_file_ids(None) == []


# ---------------------------------------------------------------------------
# collect_file_ids — SA-1905 E: run-retention file cleanup
# ---------------------------------------------------------------------------

def test_collect_file_ids_walks_nested_variables_and_dedupes():
    # Run variables nest file_refs at varying depths, incl. a 3-level tree with
    # artifacts, and a duplicate reference to the same main file.
    tree = _tree_three_level()   # f_main + f_orders + f_li + f_attrs
    variables = {
        "gen": {"result": build_file_ref(file_id="f_gen", columns=["i"])},
        "out": {"output": {"du_lieu": tree}, "metadata": {"type": "output"}},
        "dup": build_file_ref(file_id="f_gen", columns=["i"]),   # same id again
        "scalar": 123,
        "list": [build_file_ref(file_id="f_list", columns=["x"]), "noise"],
    }
    ids = collect_file_ids(variables)
    # every file in the tree + the standalone refs, each once
    assert set(ids) == {"f_gen", "f_main", "f_orders", "f_li", "f_attrs", "f_list"}
    assert len(ids) == len(set(ids))   # de-duplicated (f_gen appears twice)


def test_collect_file_ids_empty_for_no_refs():
    assert collect_file_ids({"a": 1, "b": ["x", {"c": None}]}) == []
    assert collect_file_ids(None) == []
    assert collect_file_ids({}) == []


def test_collect_file_ids_survives_reference_cycle():
    # A self-referential in-memory structure must not loop forever.
    a: dict = {"ref": build_file_ref(file_id="f_cycle", columns=["i"])}
    a["self"] = a           # cycle
    b: list = [a]
    a["list"] = b           # another cycle
    assert collect_file_ids(a) == ["f_cycle"]


def test_collect_file_ids_does_not_recurse_into_ref_metadata():
    # A file_ref's own metadata keys (file_id string, columns list) must not be
    # mistaken for more refs — only descendant_file_ids drives the ref subtree.
    ref = build_file_ref(file_id="f_only", columns=["a", "b", "c"])
    assert collect_file_ids({"x": ref}) == ["f_only"]


# ---------------------------------------------------------------------------
# synthesize_data_edit_schema
# ---------------------------------------------------------------------------

class _Node:
    def __init__(self, config=None):
        self.config = config or {}


class _Task:
    def __init__(self, output_metadata=None, status=None, node_id="", created_at="",
                 run_generation=None):
        self.output_metadata = output_metadata
        self.status = status
        self.node_id = node_id
        self.created_at = created_at
        self.run_generation = run_generation


class _DataEditNode(_Node):
    def __init__(self, config=None, name="Edit"):
        super().__init__(config=config)
        self.name = name

        class _T:
            value = "DATA_EDIT"
        self.node_type = _T()


def _table_column_keys(schema: list[dict]) -> list[str]:
    """Pull column keys out of a synthesized DATA_EDIT table schema."""
    if not schema:
        return []
    rows = schema[0].get("fields") or []
    if not rows:
        return []
    cells = rows[0].get("fields") or []
    return [c.get("key") for c in cells if c.get("key")]


def test_synthesize_data_edit_schema_drops_reserved_and_underscore_columns():
    node = _DataEditNode(config={"instruction": "Edit Orders"})
    schema = synthesize_data_edit_schema(
        node, ["__row_id", "_internal", "sku", "qty"],
    )
    assert len(schema) == 1
    assert _table_column_keys(schema) == ["sku", "qty"]


def test_synthesize_data_edit_schema_returns_empty_when_no_user_columns():
    node = _DataEditNode()
    assert synthesize_data_edit_schema(node, ["__row_id"]) == []
    assert synthesize_data_edit_schema(node, []) == []


def test_synthesize_data_edit_schema_restricts_columns_to_odata_select():
    node = _DataEditNode()
    # Working ref tracks every column; $select narrows the runtime /schema.
    schema = synthesize_data_edit_schema(
        node, ["sku", "qty", "price"],
        query_params={"$select": "qty,sku", "$top": "10"},
    )
    # $select order is honored; unselected/unknown columns dropped.
    assert _table_column_keys(schema) == ["qty", "sku"]


def _table_wrapper_props(schema: list[dict]) -> dict:
    """Pull the table-level wrapperProps out of a synthesized schema."""
    return schema[0].get("fieldConfig", {}).get("wrapperProps", {})


def test_synthesize_data_edit_schema_defaults_to_editable_table():
    node = _DataEditNode()
    schema = synthesize_data_edit_schema(node, ["sku"])
    wp = _table_wrapper_props(schema)
    assert wp.get("isAdd") is True
    assert wp.get("isDelete") is True
    assert "readonly" not in wp
    assert schema[0].get("key") == "table_data"


def test_synthesize_data_edit_schema_readonly_marks_cells_and_table():
    node = _DataEditNode()
    schema = synthesize_data_edit_schema(node, ["sku", "qty"], readonly=True)
    wp = _table_wrapper_props(schema)
    assert wp.get("readonly") is True
    assert "isAdd" not in wp and "isDelete" not in wp
    # every cell carries readonly so the FE renders inspect-only
    cells = schema[0]["fields"][0]["fields"]
    assert all(
        c.get("fieldConfig", {}).get("controlProps", {}).get("readonly") is True
        for c in cells
    )


def test_synthesize_data_edit_schema_custom_update_prefixes_table_key():
    node = _DataEditNode()
    schema = synthesize_data_edit_schema(node, ["sku"], custom_data_update=True)
    assert schema[0].get("key") == "__table_data"


def test_synthesize_data_edit_schema_injects_actions_and_settings():
    node = _DataEditNode()
    actions = [{"label": "Approve", "id": "approve"}]
    settings = [{"key": "note", "fieldConfig": {}}]
    schema = synthesize_data_edit_schema(
        node, ["sku"], actions=actions, extra_settings=settings,
    )
    assert _table_wrapper_props(schema).get("actions") == actions
    # settings are appended as siblings of the row template
    assert settings[0] in schema[0]["fields"]


def test_synthesize_data_edit_schema_instruction_overrides_label():
    node = _DataEditNode(config={"instruction": "from-config"})
    schema = synthesize_data_edit_schema(node, ["sku"], instruction="from-arg")
    assert _table_wrapper_props(schema).get("label") == "from-arg"


def test_synthesize_data_edit_schema_carries_query_params():
    node = _DataEditNode()
    schema = synthesize_data_edit_schema(
        node, ["sku"], query_params={"$filter": "qty gt 5", "$top": "10"},
    )
    assert _table_wrapper_props(schema).get("query") == {
        "$filter": "qty gt 5", "$top": "10",
    }


def test_synthesize_data_edit_schema_omits_query_when_empty():
    node = _DataEditNode()
    schema = synthesize_data_edit_schema(node, ["sku"])
    assert "query" not in _table_wrapper_props(schema)


# --- SA-1239: config-driven three-dots removal + selection toggle -----------

def test_synthesize_data_edit_schema_omits_row_more_action_flag():
    # SA-1239 (corrected): the ⋯ "More Action" menu is gated on the FE by
    # row-more-action slot presence (+ contextReadonly), NOT by a backend flag.
    # synthesize no longer emits isShowRowMoreAction — even a legacy config flag
    # is ignored.
    node = _DataEditNode(config={"isShowRowMoreAction": True})
    wp = _table_wrapper_props(synthesize_data_edit_schema(node, ["sku"]))
    assert "isShowRowMoreAction" not in wp


def test_synthesize_data_edit_schema_readonly_defaults_selection_single():
    # SA-1239 AC3: the approval (readonly) DATA_EDIT table selects with a radio.
    node = _DataEditNode()
    wp = _table_wrapper_props(
        synthesize_data_edit_schema(node, ["sku"], readonly=True)
    )
    assert wp.get("selectionMode") == "single"


def test_synthesize_data_edit_schema_editable_omits_selection_mode():
    # Backward-compatible: editable tables keep the historic multi-select
    # (FE default) — no selectionMode is emitted.
    node = _DataEditNode()
    wp = _table_wrapper_props(synthesize_data_edit_schema(node, ["sku"]))
    assert "selectionMode" not in wp


def test_synthesize_data_edit_schema_selection_mode_param_overrides():
    node = _DataEditNode()
    wp = _table_wrapper_props(
        synthesize_data_edit_schema(node, ["sku"], selection_mode="multiple")
    )
    assert wp.get("selectionMode") == "multiple"


def test_synthesize_data_edit_schema_selection_mode_from_node_config():
    node = _DataEditNode(config={"selectionMode": "multiple"})
    wp = _table_wrapper_props(synthesize_data_edit_schema(node, ["sku"]))
    assert wp.get("selectionMode") == "multiple"


def test_synthesize_data_edit_schema_none_config_omits_selection_mode():
    # SA-1239: designer picks "none" → no row selection → wrapperProps carries
    # no selectionMode at all.
    node = _DataEditNode(config={"selectionMode": "none"})
    wp = _table_wrapper_props(synthesize_data_edit_schema(node, ["sku"]))
    assert "selectionMode" not in wp


def test_synthesize_data_edit_schema_none_overrides_readonly_default():
    # An explicit "none" wins over the readonly→single default: a readonly
    # approval table whose designer chose "none" emits no selectionMode.
    node = _DataEditNode(config={"selectionMode": "none"})
    wp = _table_wrapper_props(
        synthesize_data_edit_schema(node, ["sku"], readonly=True)
    )
    assert "selectionMode" not in wp


def test_synthesize_data_edit_schema_none_param_omits_selection_mode():
    node = _DataEditNode()
    wp = _table_wrapper_props(
        synthesize_data_edit_schema(node, ["sku"], selection_mode="none")
    )
    assert "selectionMode" not in wp


# ---------------------------------------------------------------------------
# compute_node_state
# ---------------------------------------------------------------------------

class _Context:
    def __init__(self, completed=None, skipped=None):
        self.completed_nodes = set(completed or [])
        self.skipped_nodes = set(skipped or [])


class _Run:
    def __init__(self, completed=None, skipped=None):
        self.context = _Context(completed, skipped)


def test_compute_node_state_skipped_wins():
    run = _Run(skipped=["n1"], completed=["n1"])
    assert compute_node_state(run, "n1", None) == "SKIPPED"


def test_compute_node_state_completed_without_task():
    run = _Run(completed=["n1"])
    assert compute_node_state(run, "n1", None) == "COMPLETED"


def test_compute_node_state_uses_task_status_when_neither_set():
    run = _Run()
    task_pending = _Task(status="PENDING")
    task_running = _Task(status="RUNNING")
    task_failed = _Task(status="FAILED")
    assert compute_node_state(run, "n1", task_pending) == "PENDING"
    assert compute_node_state(run, "n1", task_running) == "RUNNING"
    assert compute_node_state(run, "n1", task_failed) == "FAILED"


def test_compute_node_state_default_pending_when_no_task():
    run = _Run()
    assert compute_node_state(run, "n1", None) == "PENDING"


# ---------------------------------------------------------------------------
# is_pending_node_type
# ---------------------------------------------------------------------------

def test_is_pending_node_type_covers_all_four():
    for nt in ("INPUT", "HUMAN_ACTION", "DATA_EDIT", "FILE_UPLOAD"):
        assert is_pending_node_type(nt) is True


def test_is_pending_node_type_rejects_others():
    for nt in ("TASK", "CONDITION", "COMPUTE"):
        assert is_pending_node_type(nt) is False


# ---------------------------------------------------------------------------
# find_latest_task_for_node
# ---------------------------------------------------------------------------

def test_find_latest_task_for_node_picks_max_created_at():
    tasks = [
        _Task(node_id="n1", created_at="2026-01-01T00:00:00Z"),
        _Task(node_id="n1", created_at="2026-02-01T00:00:00Z"),
        _Task(node_id="n2", created_at="2026-03-01T00:00:00Z"),
    ]
    latest = find_latest_task_for_node(tasks, "n1")
    assert latest is tasks[1]


def test_find_latest_task_for_node_none_on_miss():
    tasks = [_Task(node_id="n1", created_at="2026-01-01Z")]
    assert find_latest_task_for_node(tasks, "n999") is None


def test_find_latest_task_for_node_empty_list():
    assert find_latest_task_for_node([], "n1") is None


def test_find_latest_task_for_node_prefers_current_generation_over_created_at():
    # The gen-1 row has a LATER created_at than the gen-2 row — the authoritative
    # generation must win over the string timestamp (this is the stale-input bug).
    tasks = [
        _Task(node_id="n1", created_at="2026-09-01T00:00:00Z", run_generation=1),
        _Task(node_id="n1", created_at="2026-02-01T00:00:00Z", run_generation=2),
        _Task(node_id="n2", created_at="2026-12-01T00:00:00Z", run_generation=2),
    ]
    latest = find_latest_task_for_node(tasks, "n1")
    assert latest is tasks[1]  # gen-2 row, despite its earlier timestamp


def test_find_latest_task_for_node_none_when_absent_from_current_generation():
    # n1 ran only in gen 1; the current cycle (gen 2, via n2) never re-dispatched
    # it → honest None, NOT n1's surviving prior-cycle row. test_full / reset_test_run
    # CANCEL every prior task, so the superseded row carries CANCELLED (the status
    # is what distinguishes this from a preserved-upstream partial rerun).
    tasks = [
        _Task(node_id="n1", created_at="2026-01-01T00:00:00Z", run_generation=1,
              status="CANCELLED"),
        _Task(node_id="n2", created_at="2026-02-01T00:00:00Z", run_generation=2),
    ]
    assert find_latest_task_for_node(tasks, "n1") is None


def test_find_latest_task_for_node_treats_missing_generation_as_zero():
    # Legacy rows (run_generation=None) coexist with explicit gen 0 → same cycle.
    tasks = [
        _Task(node_id="n1", created_at="2026-01-01T00:00:00Z"),
        _Task(node_id="n1", created_at="2026-02-01T00:00:00Z", run_generation=0),
    ]
    latest = find_latest_task_for_node(tasks, "n1")
    assert latest is tasks[1]  # both gen 0 → newest created_at wins


def test_find_latest_task_for_node_keeps_preserved_upstream_after_partial_rerun():
    # Restart-from-failure rerun: upstream ran in gen 1 (COMPLETED, never
    # cancelled); the failed node re-dispatched in gen 2. The upstream input
    # panel must still resolve to its gen-1 row, not blank out.
    tasks = [
        _Task(node_id="up", created_at="2026-01-01T00:00:00Z", run_generation=1,
              status="COMPLETED"),
        _Task(node_id="failed", created_at="2026-01-02T00:00:00Z", run_generation=2,
              status="RUNNING"),
    ]
    assert find_latest_task_for_node(tasks, "up") is tasks[0]


def test_find_latest_task_for_node_none_when_prior_gen_all_cancelled():
    # Full reset (test_full / reset_test_run) CANCELS every prior task; a node
    # not re-dispatched this cycle has only cancelled rows → honest None.
    tasks = [
        _Task(node_id="n1", created_at="2026-01-01T00:00:00Z", run_generation=1,
              status="CANCELLED"),
        _Task(node_id="n2", created_at="2026-02-01T00:00:00Z", run_generation=2,
              status="RUNNING"),
    ]
    assert find_latest_task_for_node(tasks, "n1") is None
