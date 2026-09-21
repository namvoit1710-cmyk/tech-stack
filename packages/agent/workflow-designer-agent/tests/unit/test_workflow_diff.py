from __future__ import annotations

from app.layer1_domain.entities.workflow_diff import merge_definitions

BASE = {
    "name": "wf", "description": "d",
    "nodes": [
        {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
        {"id": "b", "type": "TASK", "name": "B", "config": {"x": 1}},
        {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
    ],
    "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
}


def test_change_a_node_preserves_the_rest_without_re_emitting() -> None:
    change = {"nodes": [{"id": "b", "type": "TASK", "name": "B2", "config": {"x": 2}}], "edges": []}
    merged = merge_definitions(BASE, change)
    by_id = {n["id"]: n for n in merged["nodes"]}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["b"]["name"] == "B2" and by_id["b"]["config"] == {"x": 2}  # candidate wins
    # untouched wiring survives even though the change re-emitted no edges (AC B2)
    assert {(e["source"], e["target"]) for e in merged["edges"]} == {("a", "b"), ("b", "c")}


def test_add_a_node_and_edge() -> None:
    change = {
        "nodes": [{"id": "d", "type": "TASK", "name": "D", "config": {}}],
        "edges": [{"source": "b", "target": "d"}],
    }
    merged = merge_definitions(BASE, change)
    assert {n["id"] for n in merged["nodes"]} == {"a", "b", "c", "d"}
    assert {(e["source"], e["target"]) for e in merged["edges"]} == {
        ("a", "b"), ("b", "c"), ("b", "d")}


def test_remove_a_node_drops_it_and_its_base_edges() -> None:
    change = {"nodes": [], "edges": [{"source": "a", "target": "c"}]}
    merged = merge_definitions(BASE, change, remove_node_ids=["b"])
    assert {n["id"] for n in merged["nodes"]} == {"a", "c"}
    # b's base edges (a->b, b->c) are dropped; the new a->c is kept
    assert {(e["source"], e["target"]) for e in merged["edges"]} == {("a", "c")}


def test_candidate_edge_not_duplicated_when_it_matches_a_base_edge() -> None:
    change = {"nodes": [{"id": "b", "type": "TASK", "name": "B", "config": {}}],
              "edges": [{"source": "a", "target": "b"}]}
    merged = merge_definitions(BASE, change)
    pairs = [(e["source"], e["target"]) for e in merged["edges"]]
    assert pairs.count(("a", "b")) == 1  # deduped


def test_empty_base_returns_candidate() -> None:
    change = {"name": "new", "nodes": [{"id": "a", "type": "T", "name": "A"}], "edges": []}
    merged = merge_definitions({}, change)
    assert merged["name"] == "new"
    assert [n["id"] for n in merged["nodes"]] == ["a"]


def test_names_fall_back_to_base_when_candidate_omits_them() -> None:
    change = {"nodes": [{"id": "b", "type": "TASK", "name": "B", "config": {}}], "edges": []}
    merged = merge_definitions(BASE, change)
    assert merged["name"] == "wf" and merged["description"] == "d"
