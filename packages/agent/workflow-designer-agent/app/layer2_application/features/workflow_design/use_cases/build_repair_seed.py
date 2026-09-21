from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict


def build_repair_seed(
    verdict: DiagnosisVerdict, current_definition: dict, workflow_id: str
) -> dict[str, Any]:
    """Turn a diagnosis + the workflow's current definition into an ``update_workflow``
    partial diff the ValidateRepairGate can finalize (UPDATE mode).

    Targets the failing ``workflow_id``. When the verdict blames specific nodes, the seed is
    just those nodes (current config) — the gate merges them onto the loaded graph, so
    unrelated nodes/edges survive. When no node is blamed (a run-level failure), it falls back
    to the full graph so the gate still has something to repair.
    """
    nodes_by_id = {str(n.get("id")): n for n in (current_definition.get("nodes") or [])}
    failed = [nid for nid in verdict.failed_node_ids if nid in nodes_by_id]

    if failed:
        touched = set(failed)
        seed_nodes = [nodes_by_id[nid] for nid in failed]
        # Only edges wholly inside the touched set are re-emitted; the rest are preserved by
        # the gate's merge (sending an edge that references an un-sent node would be invalid).
        seed_edges = [
            e for e in (current_definition.get("edges") or [])
            if str(e.get("source")) in touched and str(e.get("target")) in touched
        ]
    else:
        seed_nodes = list(current_definition.get("nodes") or [])
        seed_edges = list(current_definition.get("edges") or [])

    return {
        "workflow_id": workflow_id,
        "name": current_definition.get("name", ""),
        "description": current_definition.get("description", ""),
        "nodes": seed_nodes,
        "edges": seed_edges,
        "remove_node_ids": [],
    }
