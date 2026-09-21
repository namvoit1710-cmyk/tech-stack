from __future__ import annotations

from typing import Any

# SA-2157 Part B — diff an NL change onto a workflow's LOADED base, so unrelated parts survive.
#
# The control plane's PATCH replaces the whole nodes/edges arrays (it is NOT a per-node merge),
# so preservation must happen HERE, before we persist: the model need only emit the nodes it is
# adding or changing (+ an explicit remove list), and this overlays them onto the loaded base.
#
# Rules (both in the agent's own definition shape — nodes {id,type,name,config}, edges
# {source,target}):
#   nodes = candidate nodes, then every base node whose id the candidate neither re-emitted
#           nor removed (so unchanged nodes are carried over — the model need not re-send them).
#   edges = candidate edges, then every base edge whose endpoints are both still present
#           (i.e. neither endpoint was removed), de-duplicated by (source, target).
# A base edge is kept even when it touches a *changed* (re-emitted) node so a config-only edit
# does not silently disconnect it, and adding an edge is just a new candidate edge.
# ponytail: removing an edge between two nodes that both stay requires removing/re-adding a node
# — pure edge-rewiring isn't expressible here yet; add an explicit remove_edge signal if needed.


def _node_id(node: Any) -> str:
    return str(node.get("id")) if isinstance(node, dict) else ""


def merge_definitions(
    base: dict | None,
    candidate: dict | None,
    remove_node_ids: list[str] | None = None,
) -> dict:
    """Overlay ``candidate`` (the model's add/change) onto ``base`` (the loaded workflow),
    dropping any node named in ``remove_node_ids``. Returns a full definition document."""
    base = base or {}
    candidate = candidate or {}
    removed = {str(x) for x in (remove_node_ids or []) if str(x).strip()}

    base_nodes = [n for n in (base.get("nodes") or []) if isinstance(n, dict)]
    cand_nodes = [n for n in (candidate.get("nodes") or []) if isinstance(n, dict)]
    cand_ids = {_node_id(n) for n in cand_nodes}
    # A node the candidate re-emitted or that was removed is "touched"; everything else is carried.
    touched = cand_ids | removed
    merged_nodes = list(cand_nodes) + [n for n in base_nodes if _node_id(n) not in touched]

    base_edges = [e for e in (base.get("edges") or []) if isinstance(e, dict)]
    cand_edges = [e for e in (candidate.get("edges") or []) if isinstance(e, dict)]

    def ekey(edge: dict) -> tuple[str, str]:
        return (str(edge.get("source")), str(edge.get("target")))

    seen = {ekey(e) for e in cand_edges}
    merged_edges = list(cand_edges)
    for edge in base_edges:
        # Drop a base edge only when one of its endpoints was removed; keep it otherwise so a
        # config-only change to a node doesn't disconnect its untouched wiring.
        if str(edge.get("source")) in removed or str(edge.get("target")) in removed:
            continue
        if ekey(edge) in seen:
            continue
        seen.add(ekey(edge))
        merged_edges.append(edge)

    return {
        "name": candidate.get("name") or base.get("name") or "",
        "description": candidate.get("description") or base.get("description") or "",
        "nodes": merged_nodes,
        "edges": merged_edges,
    }
