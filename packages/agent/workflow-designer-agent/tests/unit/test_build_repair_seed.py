from __future__ import annotations

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict
from app.layer2_application.features.workflow_design.use_cases.build_repair_seed import (
    build_repair_seed,
)

CURRENT = {
    "name": "greet",
    "description": "d",
    "nodes": [
        {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
        {"id": "b", "type": "TASK", "name": "B", "config": {"url": "https://broken"}},
        {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
    ],
    "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
}


def test_verdict_from_diagnose_response():
    v = DiagnosisVerdict.from_dict({
        "run_id": "r1", "category": "triggering", "failed_node_ids": ["x"],
        "recommended_fix": "restart the trigger", "root_cause": "never started",
    })
    assert v.run_id == "r1"
    assert v.failed_node_ids == ["x"]
    assert v.root_cause == "never started"
    assert v.recommended_fix == "restart the trigger"


def test_seed_targets_only_the_failed_nodes_of_the_workflow():
    # B1: a verdict with non-empty failed_node_ids -> an update_workflow partial diff that
    # targets the failing workflow_id and carries only the failed nodes (current config).
    verdict = DiagnosisVerdict(
        run_id="r1", category="agent_service_communication",
        failed_node_ids=["b"], recommended_fix="fix the URL",
    )
    seed = build_repair_seed(verdict, CURRENT, workflow_id="wf-7")

    assert seed["workflow_id"] == "wf-7"
    assert seed["name"] == "greet"
    assert {n["id"] for n in seed["nodes"]} == {"b"}
    assert seed["nodes"][0]["config"]["url"] == "https://broken"  # current config carried
    # only the edge fully inside the failed set is re-emitted; the rest survive via merge
    assert seed["edges"] == []
    assert seed["remove_node_ids"] == []


def test_seed_falls_back_to_full_graph_when_no_failed_nodes_known():
    # A run-level failure with no node attribution -> re-send the whole graph so the gate can
    # still repair it (rather than an empty, unrepairable diff).
    verdict = DiagnosisVerdict(run_id="r1", category="triggering", failed_node_ids=[])
    seed = build_repair_seed(verdict, CURRENT, workflow_id="wf-7")

    assert {n["id"] for n in seed["nodes"]} == {"a", "b", "c"}
    assert len(seed["edges"]) == 2
