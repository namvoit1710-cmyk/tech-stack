from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any
import uuid


class EventTopic(str, Enum):
    """All event topic identifiers used throughout the system.

    Inherits from ``str`` so members can be passed directly wherever a
    ``str`` topic is expected — no ``.value`` access needed, and
    membership tests against plain strings work transparently.

    Override ``__str__`` so ``str()``, f-strings, and JSON serialisation
    always produce the dot-notation value (e.g. ``"run.started"``), not
    the enum name.
    """

    def __str__(self) -> str:  # noqa: D105
        return self.value

    # --- Run lifecycle ---
    # Single unified run-lifecycle topic. The RunStatusChanged event's
    # ``status`` field discriminates: started / completed / failed /
    # cancelled / paused / resumed / suspended / pending.
    RUN_STATUS_CHANGED = "run.status"

    # --- Node lifecycle ---
    # Uniform node-jump spine: every node type emits dispatched, then one
    # of completed / failed / cancelled. Retry / resume / rerun / manual
    # trigger are folded into NODE_DISPATCHED (discriminated by
    # ``dispatch_reason``); skip is folded into NODE_CANCELLED.
    NODE_DISPATCHED = "node.dispatched"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_CANCELLED = "node.cancelled"

    # --- Pending-node interactions ---
    # One pair of topics for every pending-user node type (INPUT,
    # FILE_UPLOAD, HUMAN_ACTION, DATA_EDIT); the event's ``pending_kind``
    # field discriminates.
    INPUT_REQUESTED = "input.requested"
    INPUT_SUBMITTED = "input.submitted"

    # --- DAG routing ---
    EDGE_TRAVERSED = "edge.traversed"
    NODE_INPUT = "node.input"
    NODE_OUTPUT_PRODUCED = "node.output.produced"
    NODE_APPROVAL_FLOW = "node.approval_flow.result"
    CONTEXT_UPDATED = "context.updated"
    WIRE_SKIPPED = "wire.skipped"
    WIRE_DEAD_LETTER = "wire.dead_letter"

    # --- Loop events ---
    LOOP_STARTED = "loop.started"
    LOOP_ITERATION_STARTED = "loop.iteration.started"
    LOOP_ITERATION_COMPLETED = "loop.iteration.completed"
    LOOP_COMPLETED = "loop.completed"
    LOOP_EXIT_REQUESTED = "loop.exit.requested"
    LOOP_CONTINUE_REQUESTED = "loop.continue.requested"
    # Phase 3a.3 — iteration fan-out announcements. The engine
    # enqueues body / exit tasks inline (Phase 3 Executor extraction
    # moves that out), but the Dispatcher owns the ``EdgeTraversed``
    # emission for LOOP → body / LOOP → exit edges. These two events
    # are the hand-off: the engine publishes them when it enqueues the
    # corresponding tasks, the Dispatcher consumes and emits
    # ``EdgeTraversed`` with deterministic ids.
    LOOP_BODY_DISPATCHED = "loop.body.dispatched"
    LOOP_EXIT_DISPATCHED = "loop.exit.dispatched"

    # --- Child workflow ---
    CHILD_RUN_STARTED = "child_run.started"
    CHILD_RUN_COMPLETED = "child_run.completed"
    CHILD_RUN_FAILED = "child_run.failed"
    CHILD_RUN_CANCELLED = "child_run.cancelled"

    # --- Misc ---
    ARTIFACT_ADDED = "artifact.added"
    NODE_INPUT_EDITED = "node_input.edited"
    NODE_DATA_EDITED = "node_data.edited"

    # --- AI feed (PROMPT node, agent runs) ---
    # The PROMPT handler's resolved-content feed. A first-class, enumerable
    # event type routed to the centralized WORKFLOW_EVENT_TOPIC for agent runs
    # like every other node event (NodeInput / EdgeTraversed) — NOT a per-node
    # custom topic — so it lands in the execution log and the agent's
    # centralized stream.
    AI_FEED_PRODUCED = "ai.feed.produced"

    # --- Outbox (Phase 3 Sub 3.2) ---
    OUTBOX_REQUESTED = "outbox.requested"
    OUTBOX_RESOLVED = "outbox.resolved"

    # --- Durable timers (SA-1371) ---
    # A timer is just another event-log entry, so a "sleep until" / SLA
    # timeout survives restart + replay. TimerScheduled records intent;
    # TimerFired is written by the (leader-gated) TimerDispatcher when the
    # timer's ``fire_at`` elapses; TimerCancelled retires a pending timer
    # (run cancel / node skip / superseded).
    TIMER_SCHEDULED = "timer.scheduled"
    TIMER_FIRED = "timer.fired"
    TIMER_CANCELLED = "timer.cancelled"


@dataclass
class DomainEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    root_run_id: str = ""
    depth: int = 0
    # Free-form caller-supplied run metadata, stamped onto every event by
    # ``MetadataStampingPublisher`` (looked up from the run by ``root_run_id``)
    # so it rides into the realtime envelope and centralized topic envelope
    # via ``dataclasses.asdict``. Echo-only — opaque to the engine.
    metadata: dict[str, Any] = field(default_factory=dict)


def topic_for_event(event: Any) -> str:
    """Return the canonical topic string for a domain event instance or class."""
    event_type = event if isinstance(event, type) else type(event)
    topic = getattr(EventTopic, _camel_to_upper_snake(event_type.__name__), None)
    if isinstance(topic, EventTopic):
        return topic.value
    return _camel_to_dot_lower(event_type.__name__)


def _camel_to_upper_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).upper()


def _camel_to_dot_lower(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", ".", value).lower()


# --- Run Events ---

@dataclass
class RunStatusChanged(DomainEvent):
    """Unified run-lifecycle event.

    Folds the former RunStarted / RunCompleted / RunFailed /
    RunCancelled / RunPaused / RunResumed / RunSuspended / RunPending /
    RunInputReceived events. ``status`` discriminates:

    - ``started``   — run begins executing; carries ``input_data``.
    - ``completed`` — run finished OK; carries ``output_data`` plus
      ``correlation_id`` / ``callback_topic`` for the external callback.
    - ``failed``    — run terminated on error; carries ``error`` /
      ``failed_task_id``.
    - ``cancelled`` — run cancelled; carries ``cancelled_by`` / ``reason``.
    - ``paused`` / ``resumed`` — pause / resume cycle.
    - ``suspended`` — manual-mode run waiting for a node trigger;
      carries ``completed_nodes`` / ``execution_mode``.
    - ``pending``   — auto-mode run whose only active work is awaiting
      user input on a PENDING-type node.

    Status-specific fields are populated only for their status and left
    at defaults otherwise.
    """
    run_id: str = ""
    workflow_id: str = ""
    status: str = ""
    # status="started"
    input_data: dict[str, Any] = field(default_factory=dict)
    # status="completed"
    output_data: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    callback_topic: str = ""
    # status="failed"
    error: str = ""
    failed_task_id: str = ""
    # status="cancelled"
    cancelled_by: str = ""
    reason: str = ""
    # status="suspended"
    completed_nodes: list[str] = field(default_factory=list)
    execution_mode: str = ""


# --- Child Workflow Events ---

@dataclass
class ChildRunStarted(DomainEvent):
    parent_run_id: str = ""
    parent_node_id: str = ""
    child_run_id: str = ""
    child_workflow_id: str = ""


@dataclass
class ChildRunCompleted(DomainEvent):
    """A child workflow run finished successfully."""
    parent_run_id: str = ""
    parent_node_id: str = ""
    child_run_id: str = ""
    child_workflow_id: str = ""


@dataclass
class ChildRunFailed(DomainEvent):
    """A child workflow run terminated on error."""
    parent_run_id: str = ""
    parent_node_id: str = ""
    child_run_id: str = ""
    child_workflow_id: str = ""
    error: str = ""


@dataclass
class ChildRunCancelled(DomainEvent):
    """A child workflow run was cancelled (e.g. parent-cancellation cascade)."""
    parent_run_id: str = ""
    parent_node_id: str = ""
    child_run_id: str = ""
    child_workflow_id: str = ""
    reason: str = ""


# --- Node lifecycle Events ---

@dataclass
class NodeDispatched(DomainEvent):
    """Uniform dispatch event for every node type.

    Emitted once per node execution attempt. ``dispatch_reason``
    discriminates the folded variants: ``""`` (first dispatch),
    ``"retry"``, ``"resume"``, ``"rerun"``, ``"manual_trigger"``.
    ``attempt`` is 1 on the first dispatch and increments on retry.
    """
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""
    worker_type: str = ""
    # SA-1490 Phase 2: pinned worker contract version (from ``node.spec_version``),
    # carried to the task dispatchers so the worker runs the version the workflow
    # was authored against. Empty = unpinned. Requires the executor to tolerate it
    # (SA-1490 Phase 1) BEFORE this ships.
    spec_version: str = ""
    # SA-2424: resolved per-node worker-instance pin (debug routing). Empty =
    # unpinned (normal freshest-healthy selection at the executor). Carried to
    # the task dispatchers next to spec_version; the executor tolerates it
    # (ExecuteTaskInput.target_instance_id) and fails fast if the pin is not
    # eligible, never silently routing to another replica.
    target_instance_id: str = ""
    # SA-2437: human host label for the pinned instance (from the /workers page);
    # the executor uses it as a routing fallback when target_instance_id is stale
    # (synthesized instance_ids churn per restart on the same machine).
    target_host: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    correlation_id: str = ""
    iteration_index: int | None = None
    attempt: int = 1
    dispatch_reason: str = ""
    # Routing hint for the task dispatchers (Http/Grpc). ``True`` (default)
    # is an authoritative dispatch carrying resolved input — the dispatcher
    # POSTs it to the worker. ``False`` marks a lifecycle/tracking-only
    # announcement (e.g. ``TaskCreatorConsumer``'s, which carries no resolved
    # input); the dispatcher must NOT forward it to the worker, only let it
    # reach the projector/frontend. See ``HttpTaskDispatcher``.
    dispatch_to_worker: bool = True


@dataclass
class NodeCompleted(DomainEvent):
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    status: str = "COMPLETED"
    execution_time_ms: int = 0
    iteration_index: int | None = None
    # Branching nodes (CONDITION / SWITCH) — folded from the former
    # ConditionEvaluated event. ``branch``: CONDITION → "then"/"else";
    # SWITCH → matched branch name (or "" when nothing matched).
    # ``condition_result``: True when a branch matched. Empty/None for
    # non-branching nodes.
    branch: str = ""
    expression: str = ""
    condition_result: bool | None = None


@dataclass
class NodeFailed(DomainEvent):
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    error_code: str = ""
    error_message: str = ""
    attempt: int = 0


@dataclass
class NodeCancelled(DomainEvent):
    """Emitted when a node ends without completing.

    Covers a direct cancel (cancel_run use case), a cascaded cancel
    (parent run timeout / cancellation), and a skip (branch not taken).
    ``reason`` discriminates: ``"skipped"`` / ``"branch_not_taken"`` for
    a skip, anything else for a cancel.

    The projector folds skip reasons into ``skipped_nodes`` and all
    other reasons into ``completed_nodes`` — both are terminal for
    run-completion, so skip-propagation and run-termination converge
    correctly when rebuilding from the log.
    """
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    cancelled_by: str = ""
    reason: str = ""


@dataclass
class InputRequested(DomainEvent):
    """Unified 'a node paused waiting for user input' event.

    Folds the former HumanActionRequested / DataEditRequested events.
    ``pending_kind`` discriminates: ``input`` / ``file_upload`` /
    ``human_action`` / ``data_edit``. Kind-specific fields are populated
    only for the relevant kind.
    """
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    instruction: str = ""
    parent_run_id: str = ""
    pending_kind: str = "input"
    # pending_kind="human_action"
    confirm_label: str = "Confirm"
    reject_label: str = "Reject"
    # pending_kind="data_edit"
    source_file_id: str = ""
    source_node_id: str = ""
    source_node_name: str = ""


@dataclass
class InputSubmitted(DomainEvent):
    """Unified 'user responded to a pending node' event.

    Folds the former HumanActionCompleted / DataEditCompleted events;
    ``pending_kind`` discriminates as for ``InputRequested``.
    """
    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    pending_kind: str = "input"
    # pending_kind="human_action"
    action: str = ""
    # pending_kind="data_edit"
    file_id: str = ""
    source_node_id: str = ""


# --- Node Events ---


# --- Loop Events (Phase 4c.4.2) ---------------------------------
#
# Five event types brackted by the event-driven loop dispatcher
# (Phase 4c.4.3+). Phase 4c.4.2 defines the vocabulary only; no
# runtime emitter exists yet for ``LoopStarted`` / ``LoopIterationStarted``
# / ``LoopCompleted`` / ``LoopExitRequested``, and ``LoopIterationCompleted``
# continues to be emitted in its legacy shape (only ``iteration`` + ``total``
# populated) from ``LoopNodeHandler._handle_iteration_complete``.
#
# All ``scope_stack`` fields carry ``list[dict]`` rather than
# ``list[LoopScope]`` because events flow through the messaging
# layer which JSON-serialises payloads. The dicts are produced via
# ``LoopScope.to_dict()`` and reconstructed via ``LoopScope.from_dict()``
# — both land in ``app.layer1_domain.value_objects.loop_scope``.

@dataclass
class LoopStarted(DomainEvent):
    """Emitted when a LOOP node initialises a new invocation.

    ``invocation_id`` is derived via ``derive_invocation_id(run_id,
    node_id, enclosing)`` — stable across iterations of the same
    invocation, different for each outer iteration of a nested loop.

    ``scope_stack`` excludes self — it carries the enclosing outer
    scopes only. Empty list for a top-level loop.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)
    loop_type: str = ""         # "collection" | "count" | "condition"
    mode: str = ""              # "sequential" | "parallel"
    total: int = 0              # -1 for unbounded condition loops
    parallelism: int = 1


@dataclass
class LoopIterationStarted(DomainEvent):
    """Emitted before dispatching the body entry task for iteration N.

    ``scope_stack`` INCLUDES self at the tail — i.e. the innermost
    entry is ``LoopScope(this_loop_node_id, invocation_id, iter, total).to_dict()``.
    Downstream body tasks inherit this stack via
    ``Task.with_pushed_scope``.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)
    iter: int = 0
    item: Any = None            # loop variable snapshot (item / index)


@dataclass
class LoopIterationCompleted(DomainEvent):
    """Emitted after the body of iteration N finishes.

    The legacy fields ``iteration`` and ``total`` remain populated
    by the pre-4c.4.3 emitter at ``loop_handler.py:163``. Phase 4c.4.2
    adds the new ``invocation_id`` / ``scope_stack`` / ``iter`` /
    ``outcome`` fields as optional with defaults so the legacy emitter
    continues to compile unchanged. The event-driven handler landing
    in Phase 4c.4.3 dual-writes both ``iteration`` and ``iter``.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    iteration: int = 0          # legacy alias for ``iter``
    total: int = 0
    # --- Phase 4c.4.2 additions (optional; defaults preserve legacy) ---
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)
    iter: int = 0
    outcome: str = "ok"         # "ok" | "failed" | "skipped"


@dataclass
class LoopCompleted(DomainEvent):
    """Emitted when the loop terminates (all iterations done,
    condition false, break signal, or error).

    ``reason`` sentinel values:
    - ``"all_done"`` — ran through every item / iteration
    - ``"condition_false"`` — condition loop's predicate evaluated false
    - ``"break"`` — a ``LoopExitRequested`` signal was observed
    - ``"early_exit_error"`` — condition expression raised
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)
    total_iterations: int = 0
    reason: str = "all_done"


@dataclass
class LoopBodyDispatched(DomainEvent):
    """Phase 3a.3 — engine announces it is enqueuing body tasks for a
    loop iteration. The DispatcherConsumer listens and emits one
    ``EdgeTraversed`` per ``LOOP → body_node`` edge carried in
    ``body_node_ids``.

    ``causation_id`` is the id of the event that caused this dispatch:
    the LOOP node's own task id for the first iteration, the
    ``LoopIterationCompleted`` event id for subsequent iterations.
    Deterministic ``EdgeTraversed.event_id`` ids are derived from it
    so replay dedupes consistently.

    Task creation + enqueue still happen inline in the engine; this
    event is emission-only so the Dispatcher remains the sole source
    of ``EdgeTraversed`` in production.
    """
    run_id: str = ""
    workflow_id: str = ""
    loop_node_id: str = ""
    body_node_ids: list[str] = field(default_factory=list)
    iteration_index: int | None = None
    causation_id: str = ""


@dataclass
class LoopExitDispatched(DomainEvent):
    """Phase 3a.3 — engine announces it is enqueuing exit tasks after
    the final loop iteration. The DispatcherConsumer emits one
    ``EdgeTraversed`` per ``LOOP → exit_node`` edge carried in
    ``exit_node_ids``.

    Same ``causation_id`` semantics as ``LoopBodyDispatched``.
    """
    run_id: str = ""
    workflow_id: str = ""
    loop_node_id: str = ""
    exit_node_ids: list[str] = field(default_factory=list)
    causation_id: str = ""


@dataclass
class LoopExitRequested(DomainEvent):
    """Cooperative break signal emitted by a future break / LOOP_EXIT
    node inside the body mid-iteration.

    The loop driver observes this and emits
    ``LoopCompleted(reason="break")`` once in-flight iterations
    settle. Phase 4c.4.2 defines the type only; no emitter exists
    today.
    """
    run_id: str = ""
    workflow_id: str = ""
    loop_node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoopContinueRequested(DomainEvent):
    """Cooperative continue signal emitted by a LOOP_CONTINUE node
    inside the loop body mid-iteration.

    The loop driver observes the ``continue_requested`` flag on
    ``loop_state`` at the next iteration boundary, resets it, marks
    the iteration outcome as ``"skipped"``, and advances directly
    to the next iteration without re-evaluating the condition.
    """
    run_id: str = ""
    workflow_id: str = ""
    loop_node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict[str, Any]] = field(default_factory=list)


# --- Edge Events ---

@dataclass
class EdgeTraversed(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    edge_id: str = ""
    source_node_id: str = ""
    target_node_id: str = ""
    source_port_id: str = ""
    iteration_index: int | None = None


# --- Context Events ---

@dataclass
class ContextUpdated(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""
    data_type: str = "inline"  # "inline" | "file_ref"


# --- Re-run Events ---

# --- Artifact Events ---

@dataclass
class ArtifactAdded(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    file_id: str = ""
    name: str = ""
    content_type: str = ""
    artifact_type: str = ""


# --- Node Input Events ---

@dataclass
class NodeInputEdited(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    task_id: str = ""
    previous_file_id: str = ""
    new_file_id: str = ""


# --- Node Input Events (Phase 0 of engine split) ---
#
# Emitted by the engine at the dispatch boundary — the moment a task is
# handed to its handler. This is the event the future Dispatcher service
# will emit to the Executor; today the engine emits it to itself so the
# event stream is self-sufficient (the Projector can see every input
# resolution, not just dispatch/completion pairs).

@dataclass
class NodeInput(DomainEvent):
    """Node receives its resolved input and is about to execute.

    Emitted BEFORE ``NodeDispatched``. Replay order is then:
      NodeInput → NodeDispatched → NodeOutputProduced → NodeCompleted → EdgeTraversed
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    task_id: str = ""
    resolved_input: dict[str, Any] = field(default_factory=dict)
    iteration_index: int | None = None


# --- Wire Routing Events (Phase 0 of engine split) ---
#
# Emitted structurally today; the Dispatcher extraction in Phase 2 will
# own routing and populate these richly. Defining the topics now means
# downstream consumers (Projector, UI) can subscribe before the
# extraction lands — preventing a breaking-change moment when Phase 2
# ships.

@dataclass
class WireSkipped(DomainEvent):
    """Emitted when an outgoing edge is dropped during fan-out.

    Reasons:
      - ``"port_filter"``  — edge's source_port_id did not match the
        node's completed output port (e.g. failure-port edge dropped
        when the node succeeded).
      - ``"filter_predicate"`` — future wire-filter predicate evaluated
        false. Not used today; reserved for Phase 2.
    """
    run_id: str = ""
    workflow_id: str = ""
    edge_id: str = ""
    source_node_id: str = ""
    target_node_id: str = ""
    source_port_id: str = ""
    reason: str = "port_filter"


@dataclass
class WireDeadLetter(DomainEvent):
    """Emitted when a node produced output that reached no downstream node.

    Distinct from a leaf node (which has no outgoing edges by design).
    Dead-letter means: the node HAS outgoing edges, but every one of
    them was dropped (port filter, skip propagation, unreachable
    target). A modelling signal rather than an error.
    """
    run_id: str = ""
    workflow_id: str = ""
    source_node_id: str = ""
    source_port_id: str = ""
    dropped_edge_ids: list[str] = field(default_factory=list)


# --- Node Data Edit Event (Phase 1a of engine split) ---

@dataclass
class NodeDataEdited(DomainEvent):
    """Emitted when ``edit_node_data`` commits a new ``__file_ref`` tree
    for a node — either via batched ops (``_apply_and_commit``) or
    ``reset_to_input``.

    Phase 1a: emitted BEFORE the aggregate write so the projector's
    ``variables`` dict can be rebuilt from the event log. The payload
    carries the same ``state_updates`` shape as ``NodeOutputProduced``
    (primary key plus name alias) so the reducer reuses the same code
    path.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""
    task_id: str = ""
    state_updates: dict[str, Any] = field(default_factory=dict)
    edited_by: str = ""
    reason: str = ""


# --- Approval Flow Events ---

@dataclass
class NodeApprovalFlow(DomainEvent):
    """Emitted when an APPROVAL_FLOW node finishes fetching and shaping approval data."""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""
    instruction: str = ""
    change_request_id: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    output_port: str = ""
    error: str = ""


# --- AI Feed Events ---

@dataclass
class AiFeedProduced(DomainEvent):
    """PROMPT handler feed signal — a wire-thin, agent-facing marker that a
    PROMPT node resolved its content, on the canonical ``ai.feed.produced``
    topic (``EventTopic.AI_FEED_PRODUCED``).

    Emitted by ``PromptNodeHandler`` only when ``run.metadata["source"]
    == "agent"``. It rides the standard event spine exactly like
    ``NodeInput`` / ``EdgeTraversed``: ``MetadataStampingPublisher`` stamps
    the run metadata (carrying ``source: "agent"``), so the event is
    appended to the execution log AND forwarded to the centralized
    ``WORKFLOW_EVENT_TOPIC`` for agent consumers. It deliberately does NOT
    route to a per-node custom topic — a previous design set
    ``metadata["target_topic"] = "ai.feed.<node_id>"``, which diverted the
    event off the centralized stream (nothing subscribed to it) and left a
    gap in the per-run sequence consumers resequence on.

    It carries NO inline content. A user's prompt content can exceed the
    broker's ~1 MB per-message cap, so embedding it would get dropped by
    ``EnrichingEventPublisher._enforce_size_limit`` (or rejected outright).
    The resolved content is file-backed instead: the node's
    ``NodeOutputProduced`` carries a ``state_updates[node_id]`` file_ref
    pointer (uploaded by ``apply_state_updates_as_files``), and the agent
    materialises the full text from the file service on demand — via that
    pointer or ``GET /runs/{run_id}/nodes/{node_id}/output`` — correlating
    on ``(run_id, node_id)``.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""


# --- Node Output Events (Phase 5.1) ---

@dataclass
class NodeOutputProduced(DomainEvent):
    """Carries the variable writes a node handler produced.

    One event per call to ``apply_state_updates`` /
    ``apply_state_updates_as_files`` or per inline state-update block
    in ``SuccessorProcessor.process``. The ``state_updates`` payload
    captures every key written to ``run.context.variables`` as a
    result of this node's handler returning — primary keys AND the
    name-aliases derived from the workflow definition.

    Purpose: close the Phase 4b.3 Projector gap. With this event, the
    Projector can rebuild ``run.context.variables`` from the event log
    alone, making the log authoritative for every field of the run
    state that today requires direct aggregate access.

    Emitted BEFORE ``NodeCompleted`` so replay order is:
      NodeOutputProduced → NodeCompleted → EdgeTraversed → ...

    That keeps the reducer's ``variables`` update visible before any
    downstream node's input resolution looks for its upstream's output.
    """
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""  # the primary node whose handler produced the update
    state_updates: dict[str, Any] = field(default_factory=dict)
    # Phase 2b — the handler-selected output port ("success" / "failure"
    # for port-routed node types, "out_default" otherwise). The
    # Dispatcher uses this when fanning out via ``compute_fanout`` so it
    # does not have to reconstruct the port from ``task.status``. Empty
    # string denotes "no port selection made" (legacy replay from before
    # the field existed); downstream consumers must tolerate that.
    output_port: str = ""
    # Phase 2b — for branching handlers (CONDITION / SWITCH) only.
    # Maps ``edge.id → port_id`` for each edge the handler selected. The
    # Dispatcher treats a non-empty dict as "this was a branching node,
    # use these edges directly" and bypasses port-based fan-out. Empty
    # dict denotes "not a branching decision".
    taken_edge_ports: dict[str, str] = field(default_factory=dict)


# --- Outbox Events (Phase 3 Sub 3.2) ---------------------------
#
# Side-effect handlers (DATA_EDIT file ops, HITL resume) record their
# intent as an ``OutboxRequested`` event in the same event log as the
# rest of the run. An asynchronous ``OutboxDispatcher`` picks up
# unresolved requests, performs the real call with an idempotency key,
# and appends ``OutboxResolved`` with the outcome. Replay-safe: the
# ``(run_id, event_id)`` log dedupe combined with the deterministic
# ``outbox_id`` (see ``deterministic_outbox_id``) means redelivery
# folds to the same pair.
#
# Sub 3.2 ships the event shapes + projector reducer branches +
# dispatcher infrastructure. Producer-side migration of DATA_EDIT and
# HITL is Sub 3.3 / 3.4.

@dataclass
class OutboxRequested(DomainEvent):
    """Side-effect intent recorded by a handler.

    Carries the minimal metadata the dispatcher needs to perform the
    real call idempotently. Payload bytes (e.g. CSV for ``file_upload``)
    are NOT stored here — they reach the dispatcher through a per-pod
    in-memory channel populated by the producing handler. Replay
    re-runs the handler, which re-populates the channel.

    See ``docs/design/phase-3-outbox-pattern.md`` §2-§7 for kinds and
    bytes-transport details.
    """
    outbox_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    kind: str = ""               # "file_upload" | "file_delete" | "queue_enqueue" | "stage_record"
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass
class OutboxResolved(DomainEvent):
    """Dispatcher-written terminal result of an ``OutboxRequested``.

    ``outcome`` is ``"ok"`` when the side effect succeeded, ``"failed"``
    when the dispatcher exhausted its retry budget or hit a permanent
    error. ``result`` carries kind-specific data (e.g.
    ``{"file_id": "f_abc"}`` for ``file_upload``); ``error`` is
    populated only on ``"failed"``.
    """
    outbox_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    outcome: str = "ok"          # "ok" | "failed"
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# --- Durable Timer Events (SA-1371) ---------------------------------
#
# A durable timer lets a run "sleep until" a wall-clock instant (delay,
# SLA timeout, scheduled resume). The lifecycle is fully event-sourced so
# it survives restart + replay:
#   TimerScheduled — a handler requested a timer; carries ``fire_at`` + kind.
#   TimerFired     — the leader-gated TimerDispatcher observed ``fire_at``
#                    elapsed and fired it. Fires exactly once: a deterministic
#                    ``event_id`` + the log's ``(run_id, event_id)`` dedupe make
#                    a double-fire (two pods, redelivery) a safe no-op.
#   TimerCancelled — the timer was retired before firing (run cancel, node
#                    skip, or superseded by a newer timer for the same node).

@dataclass
class TimerScheduled(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    timer_id: str = ""
    kind: str = "delay"          # "delay" | "sla" | "schedule"
    fire_at: str = ""            # ISO-8601 wall-clock instant to fire at
    task_id: str = ""


@dataclass
class TimerFired(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    timer_id: str = ""
    kind: str = "delay"
    fire_at: str = ""            # the scheduled instant (echoed for audit)


@dataclass
class TimerCancelled(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    timer_id: str = ""
    reason: str = ""             # "run_cancelled" | "node_skipped" | "superseded"
