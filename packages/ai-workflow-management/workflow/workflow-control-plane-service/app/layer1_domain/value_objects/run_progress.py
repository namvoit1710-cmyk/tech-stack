"""Run-progress percentage — pure helper (SA-2650).

Progress = the share of a run's nodes that finished **successfully**, i.e.
successfully-completed nodes + skipped (branch-not-taken) nodes, over the
workflow's total node count. **Failed nodes are deliberately excluded** from the
numerator: a failed run must read as "got X% of the way", never 100% — 100%
means the run genuinely completed. This is why the count comes from the event
log (which distinguishes NodeCompleted / NodeFailed / NodeCancelled) rather than
the run projection's ``completed_nodes``, which folds failed nodes in.

Kept pure and dependency-free so both the runs list and the child-runs list
compute it identically; the event-log scan lives in the calling use case.
"""

from __future__ import annotations

from typing import Any, Iterable

# NodeCancelled.reason values that mean "skipped" (flow didn't take this branch)
# rather than an active cancel — counted as progressed, not failed.
_SKIP_REASONS = {"skipped", "branch_not_taken"}


def compute_progress_pct(completed: int, skipped: int, total: int) -> int | None:
    """Return 0..100 progress, or ``None`` when it can't be derived.

    ``completed`` is the count of **successfully** completed nodes (NOT failed).
    ``None`` (rather than 0) signals "no meaningful value" — an empty/unknown
    topology — so the frontend renders no chip instead of a misleading 0%.
    """
    if total <= 0:
        return None
    processed = max(0, completed) + max(0, skipped)
    pct = round(processed / total * 100)
    return max(0, min(100, pct))


def reachable_path_total(topology: Any, taken_edge_ports: Any) -> int:
    """Count nodes on the path this run is actually taking (SA-2650).

    A CONDITION/SWITCH split routes to one branch; the un-taken branches are
    silently dropped (no completion, no skip event), so counting *all* graph
    nodes makes the % under-report and never reach 100%. Instead we count nodes
    **reachable** from the entry nodes, following — out of a node that has
    already decided its routing (it has an entry in ``last_taken_edge_ports``) —
    only the edges it actually took; and all outgoing edges of nodes that
    haven't decided yet (so downstream stays in the denominator until a split
    resolves). PARALLEL nodes take all their edges, so they're unaffected.

    Falls back to the full node count when topology/edges aren't resolvable.
    """
    if topology is None:
        return 0
    adjacency = getattr(topology, "adjacency", {}) or {}
    edge_map = getattr(topology, "edge_map", {}) or {}
    entry = list(getattr(topology, "entry_nodes", []) or [])
    if not entry or not edge_map:
        return len(adjacency)
    taken_edge_ports = taken_edge_ports or {}
    reachable: set[str] = set()
    stack = list(entry)
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        edges = edge_map.get(node)
        taken = taken_edge_ports.get(node)
        if edges is None:
            nexts = adjacency.get(node, [])
        elif taken:
            taken_ids = set(taken.keys())
            nexts = [getattr(e, "target_node_id", None) for e in edges if getattr(e, "id", None) in taken_ids]
        else:
            nexts = [getattr(e, "target_node_id", None) for e in edges]
        for nxt in nexts:
            if nxt and nxt not in reachable:
                stack.append(nxt)
    return len(reachable)


def count_terminal_nodes(envelopes: Iterable[Any]) -> tuple[int, int, int]:
    """Count distinct (run_id, node_id) that reached each terminal state.

    Returns ``(completed, failed, skipped)`` where ``completed`` is
    NodeCompleted only (successes), ``failed`` is NodeFailed, and ``skipped`` is
    NodeCancelled with a skip reason. Distinct per node so loop bodies / retries
    that re-emit an event for one node aren't double-counted. Mirrors
    ``GetRunExecutionLogUseCase._build_summary`` — the authoritative breakdown.
    """
    completed: set[tuple[str, str]] = set()
    failed: set[tuple[str, str]] = set()
    skipped: set[tuple[str, str]] = set()
    for env in envelopes:
        payload = getattr(env, "payload", None) or {}
        node_id = payload.get("node_id")
        if not node_id:
            continue
        key = (getattr(env, "run_id", ""), node_id)
        et = getattr(env, "event_type", "")
        if et == "NodeCompleted":
            completed.add(key)
        elif et == "NodeFailed":
            failed.add(key)
        elif et == "NodeCancelled" and payload.get("reason") in _SKIP_REASONS:
            skipped.add(key)
    return len(completed), len(failed), len(skipped)
