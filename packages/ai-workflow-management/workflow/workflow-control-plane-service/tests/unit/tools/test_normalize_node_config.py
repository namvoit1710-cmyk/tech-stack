"""tools/normalize_node_config — one transform test per design §9 table row,
idempotency, and the everything-legacy synthetic workflow."""

import copy

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from tools.normalize_node_config import (
    Report,
    _save_with_retry,
    normalize_node_dict,
    normalize_snapshot_definition,
    normalize_workflow_entity,
)


def _node(node_type: str, *, config=None, binding=None, parameters=None,
          node_id="n1", snapshot=None) -> dict:
    return {
        "id": node_id,
        "node_type": node_type,
        "config": config or {},
        "input_binding": binding or {},
        "parameters": parameters or {},
        "definition_snapshot": snapshot,
    }


def _normalize(node: dict) -> tuple[dict, Report]:
    report = Report()
    normalize_node_dict(node, where="t", report=report)
    return node, report


# ── per-row transforms ──


def test_merge_keys_and_vocabulary() -> None:
    node, report = _normalize(_node("MERGE", config={
        "wait_strategy": "wait_n", "n_required": 2, "timeout_seconds": "60",
    }))
    assert node["config"] == {
        "merge_strategy": "n_of_m", "wait_count": 2, "timeout_seconds": 60,
    }
    assert report.transforms["merge_wait_strategy_renamed"] == 1
    assert report.transforms["merge_n_required_renamed"] == 1
    assert report.transforms["merge_vocabulary_normalized"] == 1


def test_merge_canonical_key_wins_over_legacy() -> None:
    node, _ = _normalize(_node("MERGE", config={
        "merge_strategy": "any", "wait_strategy": "wait_all",
    }))
    assert node["config"]["merge_strategy"] == "any"
    assert "wait_strategy" not in node["config"]


def test_loop_items_expression_renamed() -> None:
    node, _ = _normalize(_node("LOOP", config={
        "loop_type": "collection", "items_expression": "{{$input.items}}",
    }))
    assert node["config"]["collection"] == "{{$input.items}}"
    assert "items_expression" not in node["config"]
    assert "items_expression" not in node["input_binding"]


def test_workflow_input_binding_renamed_and_folded() -> None:
    node, _ = _normalize(_node("WORKFLOW", config={
        "workflow_id": "wf-c",
        "input_binding": [{"param_name": "a", "param_value": "{{$n.out}}"}],
    }))
    assert node["config"]["input_mapping"] == {"a": "{{$n.out}}"}
    assert "input_binding" not in node["config"]


def test_parallel_dead_fields_dropped_and_preserved() -> None:
    node, report = _normalize(_node("PARALLEL", config={
        "branches": [], "max_concurrency": 4, "fail_strategy": "fail_fast",
    }))
    assert "max_concurrency" not in node["config"]
    assert "fail_strategy" not in node["config"]
    assert node["_migration_dropped"] == {
        "max_concurrency": 4, "fail_strategy": "fail_fast",
    }
    assert report.transforms["parallel_dead_fields_dropped"] == 1


def test_condition_data_rehomed_and_expression_dropped() -> None:
    rules = [{"key": "{{$a.b}}", "operations": "==", "value": "1"}]
    node, _ = _normalize(_node(
        "CONDITION",
        config={"expression": "legacy"},
        binding={"data": rules},
    ))
    assert node["config"]["data"] == rules
    assert "expression" not in node["config"]  # structured wins
    assert node["input_binding"] == {}


def test_trigger_fields_rehomed_to_config() -> None:
    # SA-2059: webhook trigger_type retired; event_topic + event_filter are the
    # event trigger's config keys (re-homed out of input_binding).
    node, _ = _normalize(_node(
        "TRIGGER",
        config={"trigger_type": "event"},
        binding={"event_topic": "orders.created", "event_filter": "$.p == 1"},
    ))
    assert node["config"]["event_topic"] == "orders.created"
    assert node["config"]["event_filter"] == "$.p == 1"
    assert node["input_binding"] == {}


def test_json_string_input_schema_parsed() -> None:
    node, report = _normalize(_node("INPUT", config={
        "input_schema": '[{"key": "x"}]',
    }))
    assert node["config"]["input_schema"] == [{"key": "x"}]
    assert report.json_string_schemas


def test_unparseable_schema_reported_not_destroyed() -> None:
    node, report = _normalize(_node("INPUT", config={
        "input_schema": "not json {",
    }))
    assert node["config"]["input_schema"] == "not json {"
    assert report.unparseable_schemas


def test_internal_snapshots_extracted() -> None:
    node, report = _normalize(_node("TASK", config={
        "input_types": {"a": "number"},
        "_node_definition": {"input_schema": [{"key": "a"}], "data_mode": "json"},
        "_node_internals": {"locked_defaults": {"m": "GET"}},
    }))
    assert node["definition_snapshot"] == {
        "input_schema": [{"key": "a"}], "data_mode": "json",
        "locked_defaults": {"m": "GET"},
    }
    assert "_node_definition" not in node["config"]
    assert "_node_internals" not in node["config"]
    assert node["config"] == {"input_types": {"a": "number"}}


def test_switch_legacy_liveness_reported() -> None:
    _, report = _normalize(_node("SWITCH", config={
        "expression": "{{$a.b}}", "cases": [{"value": "1", "target": "n2"}],
    }))
    assert report.switch_legacy_nodes  # answers design §11.3


def test_residual_violations_reported_not_fixed() -> None:
    node, report = _normalize(_node("MERGE", config={
        "merge_strategy": "bogus_value",
    }))
    assert node["config"]["merge_strategy"] == "bogus_value"
    assert any(
        v["code"] == "invalid_enum" for v in report.residual_violations
    )


# ── idempotency ──


def test_idempotent_on_canonical_node() -> None:
    canonical = _node("MERGE", config={
        "merge_strategy": "n_of_m", "wait_count": 2,
    })
    snapshot = copy.deepcopy(canonical)
    changed = normalize_node_dict(snapshot, where="t", report=Report())
    assert not changed
    assert snapshot == canonical


def test_apply_twice_equals_apply_once() -> None:
    node = _node("MERGE", config={"wait_strategy": "wait_any", "n_required": 1})
    normalize_node_dict(node, where="t", report=Report())
    once = copy.deepcopy(node)
    changed = normalize_node_dict(node, where="t", report=Report())
    assert not changed
    assert node == once


# ── synthetic everything-legacy workflow (entity + snapshot adapters) ──


def _legacy_workflow() -> Workflow:
    return Workflow(id="wf-legacy", name="Legacy", nodes=[
        WorkflowNode(
            id="m1", workflow_id="m1", node_type=NodeType.MERGE,
            config={"wait_strategy": "wait_all", "n_required": 0},
        ),
        WorkflowNode(
            id="c1", workflow_id="c1", node_type=NodeType.CONDITION,
            input_binding={"data": [
                {"key": "{{$a.b}}", "operations": ">", "value": 5},
            ]},
        ),
        WorkflowNode(
            id="p1", workflow_id="p1", node_type=NodeType.PARALLEL,
            config={"max_concurrency": 2},
        ),
        WorkflowNode(
            id="t1", workflow_id="t1", node_type=NodeType.TASK,
            worker_type="w1",
            config={"_node_internals": {"locked_defaults": {"x": 1}}},
        ),
    ])


def test_workflow_entity_adapter() -> None:
    wf = _legacy_workflow()
    report = Report()
    assert normalize_workflow_entity(wf, report) is True

    merge = wf.get_node("m1")
    assert merge.config["merge_strategy"] == "all"
    cond = wf.get_node("c1")
    assert "data" in cond.config and cond.input_binding == {}
    task = wf.get_node("t1")
    assert task.definition_snapshot == {"locked_defaults": {"x": 1}}
    assert "_node_internals" not in task.config
    # PARALLEL drops preserved at the workflow level
    assert wf.metadata["_migration_dropped"]["p1"] == {"max_concurrency": 2}

    # idempotent at the entity level too
    assert normalize_workflow_entity(wf, Report()) is False


def test_snapshot_definition_adapter() -> None:
    definition = _legacy_workflow().to_snapshot_dict()
    report = Report()
    assert normalize_snapshot_definition(definition, report) is True
    nodes = {n["id"]: n for n in definition["nodes"]}
    assert nodes["m1"]["config"]["merge_strategy"] == "all"
    assert nodes["c1"]["config"]["data"]
    assert nodes["t1"]["definition_snapshot"] == {"locked_defaults": {"x": 1}}
    # snapshot nodes don't carry the drop-preservation key
    assert "_migration_dropped" not in nodes["p1"]
    assert normalize_snapshot_definition(definition, Report()) is False


# ── CAS-aware save retry ──


class _ConflictingRepo:
    """Raises a version conflict for the first N save attempts, then accepts."""

    def __init__(self, conflicts: int):
        self._remaining = conflicts
        self.saved = []

    def save(self, workflow):
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("version conflict")
        self.saved.append(workflow)

    def get_by_id(self, workflow_id):
        # Competing writer re-introduced a legacy key — the retry path
        # must re-normalize the fresh read before saving again.
        return Workflow(id=workflow_id, name="fresh", nodes=[
            WorkflowNode(id="m1", workflow_id="m1", node_type=NodeType.MERGE,
                         config={"wait_strategy": "wait_any"}),
        ])


def test_save_with_retry_re_reads_and_renormalizes():
    repo = _ConflictingRepo(conflicts=1)
    wf = _legacy_workflow()
    normalize_workflow_entity(wf, Report())

    _save_with_retry(repo, wf)

    assert len(repo.saved) == 1
    saved = repo.saved[0]
    # The SAVED object is the fresh re-read, normalized again.
    assert saved.name == "fresh"
    assert saved.get_node("m1").config["merge_strategy"] == "any"
    assert "wait_strategy" not in saved.get_node("m1").config


def test_save_with_retry_exhausts_and_raises():
    repo = _ConflictingRepo(conflicts=99)
    wf = _legacy_workflow()
    normalize_workflow_entity(wf, Report())
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="version conflict"):
        _save_with_retry(repo, wf, attempts=3)
