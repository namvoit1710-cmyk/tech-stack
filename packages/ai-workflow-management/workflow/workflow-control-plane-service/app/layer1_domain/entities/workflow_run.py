from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal
import uuid

from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.entities.base_entity import BaseDomainEntity
from app.layer1_domain.node_spec.registry import NodeRuntime, node_types_for_runtime
from app.layer1_domain.fanout import FanoutDecision
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.events import NodeOutputProduced

# Node types whose tasks pause for user input (INPUT, HUMAN_ACTION, …). The
# layer2 ``successor_processor.PENDING_NODE_TYPES`` is the same frozenset —
# both derive it from this single layer1 runtime classification, so the
# decide/dispatch split can't drift.
PENDING_NODE_TYPES = node_types_for_runtime(NodeRuntime.PENDING)

if TYPE_CHECKING:
    from app.layer1_domain.entities.task import Task
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode


@dataclass(kw_only=True)
class GraphTopology:
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    predecessors: dict[str, list[str]] = field(default_factory=dict)
    edge_map: dict[str, list[WorkflowEdge]] = field(default_factory=dict)
    entry_nodes: list[str] = field(default_factory=list)
    leaf_nodes: list[str] = field(default_factory=list)

    @classmethod
    def from_workflow(cls, workflow: "Workflow") -> "GraphTopology":
        """Build the runtime DAG structure from a workflow definition."""
        adjacency: dict[str, list[str]] = {n.id: [] for n in workflow.nodes}
        predecessors: dict[str, list[str]] = {n.id: [] for n in workflow.nodes}
        edge_map: dict[str, list[WorkflowEdge]] = {n.id: [] for n in workflow.nodes}
        for edge in workflow.edges:
            adjacency[edge.source_node_id].append(edge.target_node_id)
            predecessors[edge.target_node_id].append(edge.source_node_id)
            edge_map[edge.source_node_id].append(edge)
        entry_nodes = [n.id for n in workflow.nodes if not predecessors[n.id]]
        leaf_nodes = [n.id for n in workflow.nodes if not adjacency[n.id]]
        return cls(
            adjacency=adjacency,
            predecessors=predecessors,
            edge_map=edge_map,
            entry_nodes=entry_nodes,
            leaf_nodes=leaf_nodes,
        )

    # ── loop body-subgraph (SA-846 runtime BFS) ──────────────────────

    def _body_entry_node(
        self, loop_node_id: str, workflow: "Workflow",
    ) -> "WorkflowNode | None":
        """The node on the loop's body edge (explicit body edge, else the
        first non-exit outgoing edge)."""
        outgoing = self.edge_map.get(loop_node_id, [])
        for edge in outgoing:
            if edge.is_loop_body():
                return workflow.get_node(edge.target_node_id)
        for edge in outgoing:
            if not edge.is_loop_exit():
                return workflow.get_node(edge.target_node_id)
        return None

    def _post_loop_reachable(self, loop_node_id: str) -> frozenset[str]:
        """Nodes reachable from the loop's exit edge target(s) — used to
        prune the body BFS so it doesn't wander into post-loop territory."""
        outgoing = self.edge_map.get(loop_node_id, [])
        exit_targets = [e.target_node_id for e in outgoing if e.is_loop_exit()]
        if not exit_targets:
            return frozenset()
        visited: set[str] = set()
        queue: list[str] = list(exit_targets)
        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            for edge in self.edge_map.get(node_id, []):
                queue.append(edge.target_node_id)
        return frozenset(visited)

    def body_subgraph(
        self, loop_node_id: str, workflow: "Workflow",
    ) -> list[str]:
        """Node ids in the loop's body subgraph (BFS from body-entry,
        terminating at exit edges, LOOP_EXIT/CONTINUE nodes, back-edges, and
        post-loop nodes). Used to clear body state between iterations."""
        body_entry = self._body_entry_node(loop_node_id, workflow)
        if body_entry is None:
            return []
        post_loop = self._post_loop_reachable(loop_node_id)
        visited: set[str] = {body_entry.id}
        queue: list[str] = [body_entry.id]
        result: list[str] = [body_entry.id]
        while queue:
            node_id = queue.pop(0)
            node = workflow.get_node(node_id)
            if node is not None and node.node_type in (
                NodeType.LOOP_EXIT, NodeType.LOOP_CONTINUE,
            ):
                continue
            for edge in self.edge_map.get(node_id, []):
                if edge.is_loop_exit():
                    continue
                target = edge.target_node_id
                if target == loop_node_id or target in post_loop:
                    continue
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
                    result.append(target)
        return result

    @staticmethod
    def _routed_edges(
        node: "WorkflowNode | None", run: "WorkflowRun", edges: list,
    ) -> list:
        """Filter a body node's outgoing edges to the ones routing actually
        took (CONDITION/SWITCH/gated-PARALLEL via ``last_taken_edge_ports``);
        no mirror entry → follow all (conservative)."""
        if node is None:
            return edges
        taken = run.context.last_taken_edge_ports.get(node.id)
        if taken and node.node_type in (
            NodeType.CONDITION, NodeType.SWITCH, NodeType.PARALLEL,
        ):
            return [e for e in edges if e.id in taken]
        return edges

    def iteration_done(
        self,
        run: "WorkflowRun",
        loop_node_id: str,
        iter_idx: int,
        workflow: "Workflow",
    ) -> bool:
        """True iff every body-subgraph node REACHED by this iteration's
        routing is recorded in ``loop_state[loop_node_id]['iter_progress'][iter_idx]``.
        Branching nodes are expanded only along their taken edges (see
        ``_routed_edges``). Returns False if the loop has no ``loop_state``;
        True for an empty body."""
        loop_state = run.context.loop_state.get(loop_node_id)
        if loop_state is None:
            return False
        iter_progress: set[str] = set(
            loop_state.get("iter_progress", {}).get(iter_idx, [])
        )
        body_entry = self._body_entry_node(loop_node_id, workflow)
        if body_entry is None:
            return True  # no body to track → trivially done
        post_loop = self._post_loop_reachable(loop_node_id)
        visited: set[str] = {body_entry.id}
        queue: list[str] = [body_entry.id]
        while queue:
            node_id = queue.pop(0)
            if node_id not in iter_progress:
                return False  # this body node hasn't run for this iter
            node = workflow.get_node(node_id)
            if node is not None and node.node_type in (
                NodeType.LOOP_EXIT, NodeType.LOOP_CONTINUE,
            ):
                continue
            edges = self._routed_edges(node, run, self.edge_map.get(node_id, []))
            for edge in edges:
                if edge.is_loop_exit():
                    continue
                target = edge.target_node_id
                if target == loop_node_id or target in post_loop:
                    continue
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
        return True

    # ── cascade-skip / reset traversal ───────────────────────────────

    def downstream_to_skip(
        self,
        start_node_id: str,
        already_skipped: set[str] | None = None,
        live_sources: set[str] | None = None,
    ) -> list[str]:
        """BFS collecting downstream nodes that should be cascade-skipped.

        A node is skipped iff every predecessor is either already skipped or
        inside ``start_node_id``'s forward-reachable cone (so it has no live
        entry once the start node is dropped). Merge-safe: a successor with any
        predecessor OUTSIDE the cone (and not already skipped) keeps its live
        path and is not skipped. Handles cycles inside the skipped subtree.
        """
        already = set(already_skipped or set())
        blocked = already | {start_node_id}

        cone: set[str] = set()
        queue: deque[str] = deque([start_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in cone:
                continue
            cone.add(node_id)
            for successor_id in self.adjacency.get(node_id, []):
                if successor_id not in cone:
                    queue.append(successor_id)

        all_nodes: set[str] = set(self.adjacency.keys()) | set(self.predecessors.keys())
        live_seeds = [n for n in all_nodes if n not in blocked and n not in cone]
        live_reachable: set[str] = set()
        queue = deque(live_seeds)
        while queue:
            node_id = queue.popleft()
            if node_id in live_reachable or node_id in blocked:
                continue
            live_reachable.add(node_id)
            for successor_id in self.adjacency.get(node_id, []):
                if successor_id in blocked or successor_id in live_reachable:
                    continue
                queue.append(successor_id)

        skip_set: set[str] = (cone - live_reachable) - already

        ordered: list[str] = []
        seen: set[str] = set()
        queue = deque([start_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id in skip_set:
                ordered.append(node_id)
            for successor_id in self.adjacency.get(node_id, []):
                if successor_id not in seen:
                    queue.append(successor_id)
        return ordered

    def downstream_to_reset(
        self, start_node_id: str, *, include_start: bool = True,
    ) -> list[str]:
        """BFS collecting every downstream node that must be reset on cascade
        rerun. Unlike :meth:`downstream_to_skip`, does NOT require predecessors
        to be skipped — once an upstream output changes, every reachable
        descendant is stale. Node ids in deterministic BFS order, deduped."""
        reset: list[str] = []
        visited: set[str] = set()
        queue: deque[str] = deque([start_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            if include_start or node_id != start_node_id:
                reset.append(node_id)
            for successor_id in self.adjacency.get(node_id, []):
                if successor_id not in visited:
                    queue.append(successor_id)
        return reset


# Keys inside one ``loop_state`` entry whose dicts are keyed by ITERATION
# INDEX. The runtime writes and reads them with an ``int`` (``iter_idx =
# int(scope.iter)``, ``iter_progress.pop(iteration_index)``), but JSON has no
# integer keys, so ``CONTEXT`` hands them back as ``str`` on every reload.
ITERATION_KEYED_LOOP_STATE_KEYS = ("iter_progress", "iter_failed_nodes")


def normalize_loop_state_keys(loop_state: Any) -> Any:
    """Restore ``int`` iteration keys after a JSON round-trip. Mutates in place.

    Without this, ``iter_progress`` silently changes type every time a run is
    reloaded, and the runtime's ``int`` accesses stop matching the stored
    ``str`` keys. Three things follow, none of which fail loudly:

    * ``iter_progress.setdefault(iter_idx, [])`` misses the reloaded bucket and
      mints a SECOND one under the int key. The dict then holds both ``"0"``
      and ``0``, and ``json.dumps`` writes both as ``"0"`` — a duplicate JSON
      key, of which ``json.loads`` keeps the LAST. The earlier body nodes'
      progress is destroyed on the next save.
    * ``iteration_done`` reads ``iter_progress[iter_idx]`` with an int, so for a
      MULTI-NODE body it sees only the node recorded since the last reload,
      returns False forever, and the iteration never completes — the loop hangs
      with no error. A single-node body always survives, which is why this went
      unseen: 10 of the 11 loops in production have a one-node body.
    * ``_cleanup_iteration_state``'s ``iter_progress.pop(iteration_index)`` is a
      no-op against str keys, so finished iterations are never pruned and the
      bucket grows for the life of the run.

    Measured on prod-br10 / tenant-1: **11 of 11 runs carrying ``iter_progress``
    have str keys**, and every one retains every iteration it ever ran
    (``{"0": [...], "1": [...], "2": [...], "3": [...]}`` on a finished
    ``total=4`` loop) — the pop has never once fired in production.

    ``orchestration_engine`` already saves the run after
    ``maybe_enqueue_loop_iteration`` specifically so "a multi-node worker body
    would not lose earlier body nodes' progress between per-node completions and
    stall". That save is correct and necessary — but on its own it is what
    performs the destructive merge described above. Normalising here is what
    makes it do what it says.

    Applied in ``__post_init__`` rather than at each deserialize site so no
    future repository can reintroduce the drift: the invariant belongs to
    ``RunContext``, not to HANA.
    """
    if not isinstance(loop_state, dict):
        return loop_state
    for state in loop_state.values():
        if not isinstance(state, dict):
            continue
        for key in ITERATION_KEYED_LOOP_STATE_KEYS:
            bucket = state.get(key)
            if isinstance(bucket, dict):
                state[key] = _coerce_iteration_keys(bucket)
    return loop_state


def _coerce_iteration_keys(bucket: dict) -> dict:
    """``{"0": [...]}`` → ``{0: [...]}``, merging a str/int pair if both exist.

    A bucket that already holds both forms is the damaged shape this function
    exists to prevent, so it is repaired by UNION rather than by letting one
    side win — picking a winner is exactly what ``json.loads`` does, and it
    picks the wrong one. A key that is not an iteration index is left untouched
    instead of being dropped.
    """
    out: dict = {}
    for raw_key, value in bucket.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            out[raw_key] = value
            continue
        existing = out.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            out[key] = existing + [v for v in value if v not in existing]
        else:
            out[key] = value
    return out


@dataclass(kw_only=True)
class RunContext:
    variables: dict[str, Any] = field(default_factory=dict)
    loop_state: dict[str, Any] = field(default_factory=dict)
    completed_nodes: set[str] = field(default_factory=set)
    current_node: str = field(default_factory=str)
    skipped_nodes: set[str] = field(default_factory=set)
    # SA-846 cycle execution — per-node visit counter. ``0`` (absent) is
    # the first visit; each time an edge fans out to a node that already
    # has a terminal task the dispatcher bumps this BEFORE the successor
    # task is created, so ``TaskFactory.create_task`` mints a fresh
    # ``{run}-{node}-v{n}`` id. Every event id derived from the task id
    # (NodeInput / NodeOutputProduced attempt correlation / idempotency
    # key) becomes per-visit unique, which is what lets a cycle's second
    # iteration survive the event-log ``(run_id, event_id)`` dedupe.
    node_visits: dict[str, int] = field(default_factory=dict)
    # SA-846 Mode A — deferred revisits. Node ids where a taken edge
    # arrived while the node's latest task had already STARTED
    # (RUNNING / PENDING / WAITING / RETRYING — a READY task hasn't run
    # yet and absorbs arrivals losslessly). Instead of silently dropping
    # the arrival, the dispatch gates record it here and
    # ``check_run_completion`` drains it once the node quiesces: the
    # node re-runs exactly once per window regardless of how many
    # arrivals landed during it (set semantics).
    pending_revisits: set[str] = field(default_factory=set)
    triggered_nodes: set[str] = field(default_factory=set)
    child_runs: dict[str, str] = field(default_factory=dict)
    topology: GraphTopology = field(default_factory=GraphTopology)
    # Phase 4c.4.4 — scope-aware merge quorum buffers. Keyed on
    # ``(merge_node_id, innermost_invocation_id, innermost_iter)``
    # so merges inside nested / parallel loop bodies don't share
    # buffers across iterations. Value is the set of predecessor
    # node ids that have reported completion (or skip) for this
    # specific iteration. Tasks outside any loop use the sentinel
    # key ``("", -1)`` — the ``MergeNodeHandler`` then behaves
    # identically to the pre-4c.4.4 logic for those.
    merge_quorum_buffers: dict[tuple[str, str, int], set[str]] = field(
        default_factory=dict
    )
    # Per-node "last output port" — set by ``apply_state_updates_as_files``
    # alongside each ``NodeOutputProduced`` emission. Read by
    # ``GetNodeDataUseCase`` so the Output panel can distinguish "took the
    # success port" from "took the failure port" (TASK) or "took the false
    # branch edge" (CONDITION/SWITCH). Not part of the file_ref tree — a
    # separate scalar mapping so it never feeds through ``extract_scalars``.
    last_output_ports: dict[str, str] = field(default_factory=dict)
    # Per-node ``{edge_id: port_id}`` map for branching nodes (CONDITION,
    # SWITCH). The dispatcher still drives routing from the event; this
    # mirror is purely for the read-side Output panel.
    last_taken_edge_ports: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Cheap for the 6,105 of 6,116 runs that have no loop_state at all.
        if self.loop_state:
            normalize_loop_state_keys(self.loop_state)


@dataclass(frozen=True)
class EntryDispatch:
    """One entry node + how the shell should launch it.

    * ``"inline"`` — a PENDING-category node (INPUT / HUMAN_ACTION /
      DATA_EDIT / FILE_UPLOAD): the shell runs its handler synchronously
      (via ``_exec_uniform``) so auto-provisioning + projection finish
      before the call returns.
    * ``"async"`` — TASK / BUILTIN node: the shell saves a placeholder task
      and enqueues an ``ExecuteNodeAction`` for the worker.
    """

    node_id: str
    mode: Literal["inline", "async"]


@dataclass(frozen=True)
class EntryDecision:
    """Pure outcome of :meth:`WorkflowRun.decide_entry`.

    The ordered per-entry dispatch plan. Order mirrors the original
    single-loop dispatch exactly: a mixed run like ``[INPUT, TASK,
    HUMAN_ACTION]`` must dispatch inline → async → inline, so the shell
    iterates ``dispatches`` in this order rather than batching by mode.
    """

    dispatches: tuple[EntryDispatch, ...]


@dataclass(frozen=True)
class CompletionDecision:
    """Pure outcome of :meth:`WorkflowRun.decide_completion`.

    ``kind`` is the lifecycle transition (if any) the run should make given
    the latest task per node:

    * ``"none"`` — run is already terminal, or still has live non-pending
      work; the shell does nothing.
    * ``"failed"`` — a relevant task failed; ``failed_task_id`` names it so
      the shell can format the run-failure message.
    * ``"completed"`` — the run has quiesced (no active, no failed tasks).
    * ``"pending"`` — every active task is awaiting user input and the run
      is still ``RUNNING`` (transition out of RUNNING only).

    ``kind`` is deliberately the *quiescence judgement only* — it does NOT
    encode the SA-846 deferred-revisit drain. Draining dispatches tasks
    (I/O) and, when it dispatches nothing, the run must still fall through
    to this same quiescence outcome. So the shell sequences the drain
    between the ``failed`` check and applying ``completed``/``pending``,
    reading ``run.context.pending_revisits`` directly (see the
    ``check_run_completion`` shell). Likewise the failure-message string and
    the output-data payload are assembled by the shell — both need a
    collaborator (the workflow node lookup / the projector view) and so
    stay out of this pure value.
    """

    kind: Literal["none", "failed", "completed", "pending"]
    failed_task_id: str = ""


@dataclass(kw_only=True)
class WorkflowRun(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    workflow_version: str = "1"
    # FK into ``workflow_snapshots`` — the immutable workflow definition
    # this run executes against. Set at run-creation time; the engine
    # reads the workflow through this id (via
    # ``OrchestrationEngine._load_workflow_for_run``) instead of the live
    # ``workflow_repo``, so mid-run edits don't leak in.
    workflow_snapshot_id: str = ""
    status: RunStatus = RunStatus.PENDING
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] | None = None
    context: RunContext = field(default_factory=RunContext)
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    depth_level: int = 0
    root_run_id: str = ""
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    tenant_id: str = "system"
    correlation_id: str = ""
    callback_topic: str = ""
    is_test: bool = False
    test_mode: str = ""  # "" = production, "node" = single-node test, "full" = full workflow test
    execution_mode: str = "auto" # "auto" | "manual"
    # Phase M.2.1: monotonic version for optimistic-CAS saves.
    # ``IRunRepository.save`` increments this in place on success and raises
    # ``OptimisticConcurrencyError`` when it no longer matches the persisted
    # row. Default 0 is the sentinel "never persisted or freshly constructed"
    # — the first successful save bumps to 1 regardless of initial value.
    version: int = 0
    # G-guard: coarse counter bumped only on semantic "new execution"
    # (test-run reset, future restart-from-failure). Orthogonal to
    # ``version`` which bumps on every save. Tasks stamp ``run_generation``
    # from this at creation; callbacks reject tasks whose stamp is stale.
    generation: int = 0
    # Per-invocation overrides for the workflow's ``variables`` defaults,
    # exposed to expressions as ``{{$variable.X}}``. Authoritative on the
    # ROOT run only — sub-workflows inherit through the
    # ``ResolutionScopeBuilder`` chain so child rows never need a copy.
    # Effective view assembled by ``ResolutionScopeBuilder._effective_user_variables``.
    user_variables: dict[str, Any] = field(default_factory=dict)
    # Free-form caller-supplied metadata, captured at ``POST /runs`` and
    # echoed back, unchanged, onto every outbound external message (the
    # centralized workflow-event topic, the realtime stream, the completion
    # callback, and task dispatch to workers). Opaque to the engine — never
    # interpreted. Authoritative on the ROOT run; child runs inherit it via
    # ``root_run_id`` lookup (see ``MetadataStampingPublisher``), so their
    # events carry the root's metadata too.
    metadata: dict[str, Any] = field(default_factory=dict)
    # SA-2558: run attribution. The display label of the caller who triggered
    # this run, derived server-side from the verified BFF session / Bearer JWT
    # (name -> email -> id), NOT from the request body — so it cannot be spoofed.
    # ``"system"`` for automated / M2M / schedule-fired runs; ``None`` on
    # historical rows created before the column existed. Stamped once at
    # creation and never mutated across status transitions.
    triggered_by: str | None = None

    def __post_init__(self) -> None:
        # Sync is_test ↔ test_mode for backward compatibility
        if self.is_test and not self.test_mode:
            self.test_mode = "node"
        elif self.test_mode and not self.is_test:
            self.is_test = True
        # Invariant: root_run_id is never empty. Top-level runs are their own root;
        # child runs pass the inherited value explicitly via _start_child_workflow.
        if not self.root_run_id:
            self.root_run_id = self.id

    @classmethod
    def create(cls, workflow_id: str, **kwargs: Any) -> "WorkflowRun":
        if not workflow_id or not workflow_id.strip():
            raise ValueError("workflow_id must not be empty")
        return cls(workflow_id=workflow_id.strip(), **kwargs)

    def decide_completion(
        self,
        latest_tasks: list["Task"],
        *,
        view_skipped: frozenset[str],
    ) -> CompletionDecision:
        """Pure quiescence judgement — what lifecycle transition this run
        should make given the latest task per node. No I/O, no collaborators.

        The shell resolves the inputs first: ``latest_tasks`` is the latest
        task per node already restricted to the workflow's current nodes
        (stale tasks for deleted nodes filtered out); ``view_skipped`` is the
        projector-view skip set unioned with this run's own skip set (the
        engine's ``_view_skipped``). Everything the returned decision implies
        — failure-message formatting, output-data assembly, the SA-846
        revisit drain, CAS persistence, events, callbacks — is the shell's
        job; see :class:`CompletionDecision`.
        """
        # Already terminal (no recovery) — nothing to decide. A FAILED run is
        # NOT terminal here: late completions can resolve every failure and
        # recover the run (FAILED -> COMPLETED), so it must be re-evaluated.
        if self.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            return CompletionDecision("none")

        # Exclude tasks for nodes the run skipped — a skipped node's leftover
        # PENDING/READY task must not keep the run from reaching quiescence.
        # Exception: a node un-skipped by a revisit (a taken edge cleared its
        # AGGREGATE skip AND bumped its visit counter) is active again — its
        # fresh task must count even while the projector view still holds the
        # stale skip (the EdgeTraversed un-skip fold may lag this frame).
        agg_skipped = self.context.skipped_nodes
        visits = self.context.node_visits
        relevant_tasks = [
            t for t in latest_tasks
            if t.node_id not in view_skipped
            or (t.node_id not in agg_skipped and visits.get(t.node_id, 0) > 0)
        ]

        # A FAILED task fails the run — unless it carries the ``skipped``
        # marker (a deliberately-skipped branch leaf, not a real failure).
        failed_task = next(
            (t for t in relevant_tasks
             if t.status == TaskStatus.FAILED
             and not (t.output_metadata or {}).get("skipped")),
            None,
        )
        if failed_task is not None:
            return CompletionDecision("failed", failed_task_id=failed_task.id)

        non_terminal = {
            TaskStatus.PENDING, TaskStatus.WAITING,
            TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.RETRYING,
        }
        active_tasks = [t for t in relevant_tasks if t.status in non_terminal]

        # No non-terminal task left and nothing failed -> the run has quiesced.
        if not active_tasks:
            return CompletionDecision("completed")

        # All active work is awaiting user input -> PENDING. Only a transition
        # out of RUNNING; an already-PENDING run stays put.
        if (
            self.status == RunStatus.RUNNING
            and all(t.status == TaskStatus.PENDING for t in active_tasks)
        ):
            return CompletionDecision("pending")

        return CompletionDecision("none")

    def decide_successors(
        self,
        completed_task: "Task",
        workflow: "Workflow",
        topology: "GraphTopology",
    ) -> FanoutDecision:
        """Pure downstream-routing decision for a just-completed task.

        Given the completed task, the workflow definition, and the prepared
        topology, decide which outgoing edges fan out (via
        ``FanoutDecision.compute``). No I/O, no collaborators — reads only this
        run's context and the passed-in arguments. The shell owns adding the
        source node to ``run.context.completed_nodes`` and dispatching the
        returned decision via ``SuccessorProcessor.process``.
        """
        source_node = workflow.get_node(completed_task.node_id)

        # Resumed PENDING nodes (HUMAN_ACTION confirm/reject, INPUT, …)
        # record their routing decision in ``last_output_ports`` (set by
        # the resume use case via ``apply_state_updates_as_files``). Feed
        # it to ``FanoutDecision.compute`` as the port filter: opposite-port edges
        # (e.g. the confirm edge on a reject) are not traversed, while
        # port-less edges still continue. Without this, the pending types
        # — which aren't in ``_PORT_ROUTED_TYPES`` — fell through to the
        # traverse-ALL-edges default, and since SA-846 retired the
        # skipped-nodes dispatch filter, a reject re-fired the confirm
        # successor as a fresh ``-v{n}`` revisit.
        port_override = None
        edges_for_fanout: Any = topology.edge_map
        if source_node is not None and source_node.node_type in PENDING_NODE_TYPES:
            recorded = self.context.last_output_ports.get(completed_task.node_id, "")
            if recorded and recorded != "out_default":
                port_override = recorded
            # A resumed pending node may have classified its outgoing edges
            # explicitly (HUMAN_ACTION confirm/reject). The port filter
            # below only sees ``source_port_id`` — a legacy
            # ``condition="reject"``-labeled edge carries no port id, passes
            # as "port-less", and fires the opposite branch (which also
            # un-skips it under SA-846 taken-edge semantics, so BOTH
            # branches ran). When the resume recorded the exact taken-edge
            # set, fan out over those edges only.
            recorded_taken = self.context.last_taken_edge_ports.get(
                completed_task.node_id,
            ) or {}
            if recorded_taken:
                edges_for_fanout = [
                    e for e in topology.edge_map.get(completed_task.node_id, [])
                    if e.id in recorded_taken
                ]

        return FanoutDecision.compute(
            causation_id=completed_task.id,
            source_node_id=completed_task.node_id,
            source_node_type=(
                source_node.node_type if source_node else NodeType.TASK
            ),
            source_task_status=completed_task.status,
            outgoing_edges=edges_for_fanout,
            expected_port_override=port_override,
        )

    def decide_entry(
        self,
        entry_node_ids: list[str],
        workflow: "Workflow",
    ) -> EntryDecision:
        """Pure: classify each entry node as inline (PENDING-category) vs
        async dispatch, in order, dropping ids absent from the workflow.
        No I/O, no collaborators.

        The shell (``handle_entry_nodes``) performs the dispatch each item
        implies: task creation, ``_exec_uniform`` for inline, ``task_repo.
        asave`` + ``queue.enqueue`` for async. ``self`` is unread today —
        entry mode is purely node-type-driven (the run's
        ``execution_mode`` / ``test_mode`` gate *successor* dispatch, not
        entry) — but this stays a method for the ``decide_*`` family and any
        future per-run gating.
        """
        dispatches = []
        for entry_node_id in entry_node_ids:
            node = workflow.get_node(entry_node_id)
            if node is None:
                continue
            mode = "inline" if node.node_type in PENDING_NODE_TYPES else "async"
            dispatches.append(EntryDispatch(node_id=entry_node_id, mode=mode))
        return EntryDecision(dispatches=tuple(dispatches))

    def decide_loop_iteration(
        self,
        loop_node_id: str,
        iter_idx: int,
        workflow: "Workflow",
        topology: "GraphTopology",
    ) -> bool:
        """Pure: has every body-subgraph node REACHED by iteration
        ``iter_idx``'s routing completed (i.e. recorded in
        ``loop_state[loop_node_id]['iter_progress'][iter_idx]``)?

        The SA-846 routing-aware body-done BFS — reads only this run's
        context plus the workflow / topology args, with zero collaborators.
        The loop shell (``maybe_enqueue_loop_iteration``) uses the answer to
        decide whether to enqueue the next ``LoopIterationAction``.
        """
        return topology.iteration_done(
            run=self,
            loop_node_id=loop_node_id,
            iter_idx=iter_idx,
            workflow=workflow,
        )

    def plan_state_updates(
        self,
        wrapped_updates: dict[str, Any],
        workflow: "Workflow",
        *,
        source_node_id: str = "",
        causation_id: str = "",
        output_port: str = "",
        taken_edge_ports: dict[str, str] | None = None,
        attempt_id: str = "",
    ) -> NodeOutputProduced:
        """Pure: given the already-file-ref-wrapped state updates, build the
        full write-set and the ``NodeOutputProduced`` event that carries it.

        No I/O, no collaborators, no mutation. The shell
        (``apply_state_updates_as_files``) wraps each value to a file-ref
        first (the only I/O the write-set needs), then publishes the returned
        event, invalidates the view cache, and writes the read-side port
        mirrors (``last_output_ports`` / ``last_taken_edge_ports``).
        """
        # Build the full write-set — primary keys plus name-aliases — exactly
        # matching what the projector replays to rebuild ``variables``.
        applied: dict[str, Any] = {}
        for key, value in wrapped_updates.items():
            applied[key] = value
            node = workflow.get_node(key)
            if node and node.name and node.name != key:
                applied[node.name] = value

        primary_node_id = source_node_id or next(iter(wrapped_updates.keys()), "")
        # ``attempt_id`` (typically ``task.idempotency_key``) is folded into
        # the correlation so reruns of the same (run, node) — which keep the
        # same ``task.id`` — produce a fresh event_id and don't dedupe with the
        # prior attempt's ``NodeOutputProduced`` in the event log. Empty
        # ``attempt_id`` keeps the legacy correlation for callers without a
        # task in scope (e.g. cross-run propagation).
        correlation = (
            f"output:{primary_node_id}:{attempt_id}"
            if attempt_id
            else f"output:{primary_node_id}"
        )
        event_id = EventId.derive(causation_id or self.id, correlation)
        # Strict wire-thin: file_refs ride the wire as pointer + presentation +
        # hierarchy only; no pre-materialised scalar cache, so the event stays
        # under broker per-message limits.
        return NodeOutputProduced(
            event_id=event_id,
            run_id=self.id,
            workflow_id=self.workflow_id,
            node_id=primary_node_id,
            state_updates=applied,
            output_port=output_port,
            taken_edge_ports=dict(taken_edge_ports) if taken_edge_ports else {},
            root_run_id=self.root_run_id or self.id,
            depth=self.depth_level,
            source="workflow-control-plane",
        )
