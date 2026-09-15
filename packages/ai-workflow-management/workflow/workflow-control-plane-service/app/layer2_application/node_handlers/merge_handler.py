"""MERGE node handler: fan-in, waits for multiple branches to complete."""

from __future__ import annotations

from typing import Any

import logging

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.exceptions import InputRequirementError
from app.layer1_domain.node_spec.specs import MergeConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.read.run_state import (
    completed_nodes_of,
    skipped_nodes_of,
)
from app.layer2_application.node_handlers.inline_bases import (
    PureInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory

# Sentinel stored INSIDE the scope's quorum buffer once the merge has fired.
# A buffer member (not a separate RunContext field) so it persists, resets,
# and scopes exactly like the buffer itself: per-iteration loop cleanup and
# test-run resets discard it together with the arrival set, and no
# repo/projector schema change is needed. Excluded from quorum counting.
_FIRED = "__fired__"


def _merge_reachable_from(topology: GraphTopology, start: str, target: str) -> bool:
    """True if ``target`` is reachable from ``start`` following forward edges.

    Distinguishes a genuine loop back-edge from an uneven fan-in straggler
    (SA-1757): the source of a back-edge into the merge is DOWNSTREAM of the
    merge (the merge can reach it), so ``source -> merge`` closes a cycle and
    opens a NEW quorum round. A normal upstream predecessor arriving late is
    NOT reachable from the merge and stays a straggler. Bounded by a visited
    set so it terminates on cyclic graphs; returns ``False`` when the topology
    carries no adjacency (degrades to the prior at-most-once behavior).
    """
    adjacency = getattr(topology, "adjacency", None) or {}
    if not adjacency:
        return False
    seen: set[str] = set()
    stack = list(adjacency.get(start, []))
    while stack:
        node_id = stack.pop()
        if node_id == target:
            return True
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(adjacency.get(node_id, []))
    return False


class MergeNodeHandler(PureInlineHandlerBase):
    # Post-wired by bootstrap once the view store exists — the handler registry
    # is built before it, the same ordering the WAIT handler's CreateTimerUseCase
    # works around. ``None`` degrades to the aggregate, which is what every
    # single-frame unit harness supplies.
    _view_store: Any = None

    def __init__(self, run_context_view_store: Any = None) -> None:
        self._view_store = run_context_view_store

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()

        # Phase 6 — uniform execution fields. Default to "deferred"
        # (quorum not met); overwritten to "immediate" below when
        # quorum is satisfied.
        result.output_port = "out_default"
        result.completion_mode = "deferred"

        # Canonical keys only (NodeSpec Phase 3): merge_strategy carries
        # the handler vocabulary all|any|n_of_m; legacy wait_strategy /
        # n_required / wait_* values were normalized away by migration.
        cfg = MergeConfig.from_node(node)
        inputs_config = cfg.inputs
        wait_strategy = cfg.merge_strategy
        wait_count = cfg.wait_count
        if isinstance(wait_count, (int, float)) and not isinstance(wait_count, bool):
            n_required = int(wait_count)
        else:
            n_required = 0
        on_branch_failure = cfg.on_branch_failure
        defaults = cfg.defaults
        predecessor_ids = topology.predecessors.get(node.id, [])
        # The quorum buffer below is EPHEMERAL — it has never been persisted,
        # and it never needed to be, because these two sets rebuilt it on the
        # next arrival. DROP CONTEXT emptied them, which left the buffer as the
        # only record of a quorum and it dies between arrivals: each
        # predecessor completes in its own ContinueRunAction, on its own
        # reloaded run, and counts only itself. A two-predecessor ``all`` merge
        # then never fires and the run hangs with no error.
        completed = completed_nodes_of(run.id, self._view_store, run)
        skipped = skipped_nodes_of(run.id, self._view_store, run)

        # Phase 4c.4.4 — scope-aware quorum buffer. Merges inside a
        # parallel or nested loop body need per-iteration isolation
        # so the buffer doesn't leak state between iterations. We
        # key the buffer on ``(node_id, invocation_id, iter)`` when
        # the completed task carries a populated loop scope; for
        # tasks outside any loop we fall back to the sentinel key
        # ``(node_id, "", -1)`` which gives exactly one buffer per
        # run per merge node — equivalent to the pre-4c.4.4 logic.
        scope = completed_task.get_innermost_scope() if completed_task else None
        if scope is not None and scope.invocation_id:
            buffer_key = (node.id, scope.invocation_id, scope.iter)
        else:
            buffer_key = (node.id, "", -1)
        buffer = run.context.merge_quorum_buffers.setdefault(buffer_key, set())
        # Record the predecessor that just completed (if the merge
        # was reached by a predecessor, not by a direct call).
        if completed_task and completed_task.node_id in predecessor_ids:
            buffer.add(completed_task.node_id)
        # Seed the buffer with any predecessors already in the
        # run's completed_nodes / skipped_nodes. This is needed for
        # merges OUTSIDE a loop where legacy edge fan-in adds nodes
        # to ``completed_nodes`` without going through the buffer —
        # and for the first merge hit in a scope where the buffer
        # was just created empty.
        for pred in predecessor_ids:
            if pred in completed or pred in skipped:
                buffer.add(pred)

        # At-most-once per scope (SA-846): once this buffer's quorum has
        # fired, any later arrival is a straggler on an uneven fan-in —
        # NOT a new round. Absorb it: the arrival is recorded in the
        # buffer above, and "absorbed" terminates the revisit task
        # WITHOUT routing ("deferred" would leave the -v{n} task READY
        # forever and stall run completion; re-firing ran the downstream
        # subtree once per straggler under ``any``/``n_of_m``).
        #
        # SA-1757 cycle re-arm: a deliberate loop back-edge re-entering the
        # merge is NOT a straggler — its source is DOWNSTREAM of the merge
        # (the merge can reach it), so its arrival opens a NEW quorum round.
        # Detect that via reachability and, when found, clear the fired
        # marker + arrival set and fall through to re-evaluate quorum so the
        # downstream subtree runs again. A plain upstream straggler (not
        # reachable from the merge, or no completed_task/adjacency available)
        # is still absorbed, preserving the at-most-once contract.
        if _FIRED in buffer:
            pred_id = completed_task.node_id if completed_task else None
            is_cycle_reentry = (
                pred_id is not None
                and pred_id in predecessor_ids
                and _merge_reachable_from(topology, node.id, pred_id)
            )
            if not is_cycle_reentry:
                result.completion_mode = "absorbed"
                return result
            buffer.clear()
            buffer.add(pred_id)

        n_done = len(buffer - {_FIRED})
        n_total = len(predecessor_ids)
        should_proceed = False
        if wait_strategy == "all":
            should_proceed = n_done == n_total
        elif wait_strategy == "any":
            should_proceed = n_done > 0
        elif wait_strategy == "n_of_m":
            should_proceed = n_done >= n_required if n_required else n_done == n_total
        else:
            should_proceed = n_done == n_total
        if not should_proceed:
            return result

        # Quorum met — switch to immediate mode for Phase 6 uniform path.
        buffer.add(_FIRED)
        result.completion_mode = "immediate"

        # The ``completed_predecessors`` list (used below for
        # data merging) is still derived from the run's node sets
        # — predecessor data lives in ``run.context.variables``,
        # not in the buffer, so scope-aware keying doesn't affect it.
        completed_predecessors = [
            p for p in predecessor_ids if p in completed or p in skipped
        ]
        all_done = n_done == n_total
        merged_data: dict = {}
        if inputs_config:
            for input_spec in inputs_config:
                source_id = input_spec.get("source", "")
                key = input_spec.get("key", source_id)
                required = input_spec.get("required", True)
                if source_id in completed:
                    merged_data[key] = run.context.variables.get(source_id, {})
                elif source_id in skipped:
                    merged_data[key] = defaults.get(key, {})
                elif not required:
                    merged_data[key] = defaults.get(key, {})
                elif on_branch_failure == "partial":
                    merged_data[key] = defaults.get(key, {})
                elif key in defaults:
                    # SA-1760: a required source that hasn't arrived (possible
                    # under any/n_of_m early fire) falls back to a DECLARED
                    # default when one exists — documented, not silent.
                    merged_data[key] = defaults[key]
                else:
                    # SA-1760: required, not arrived, no default, not
                    # partial-mode → fail loud instead of silently dropping
                    # the key from merged_data (the old behavior left
                    # downstream reads of the required input silently absent).
                    raise InputRequirementError(
                        f"MERGE node {node.id}: required input {key!r} "
                        f"(source {source_id!r}) is unavailable at fire time "
                        f"(strategy={wait_strategy}) and no default is configured."
                    )
        else:
            for pred_id in predecessor_ids:
                if pred_id in completed:
                    merged_data[pred_id] = run.context.variables.get(pred_id, {})
                elif pred_id in skipped:
                    merged_data[pred_id] = {}
                else:
                    logging.getLogger(__name__).warning(
                        "Merge node %s: predecessor %s is neither completed nor skipped "
                        "(wait_strategy=%s, all_done=%s) — state may be inconsistent",
                        node.id, pred_id, wait_strategy, all_done,
                    )
        result.state_updates[node.id] = {
            "output": merged_data,
            "metadata": {
                "type": "merge",
                "input_count": len(merged_data),
                "wait_strategy": wait_strategy,
            },
        }

        return result
