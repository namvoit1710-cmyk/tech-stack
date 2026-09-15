"""Domain exceptions with error classification.

Error types follow the architecture spec:
- VALIDATION_ERROR / EXPRESSION_ERROR: Not retryable, fail immediately.
- WORKER_TIMEOUT / WORKER_ERROR / INFRASTRUCTURE_ERROR: Retryable.
- PERMISSION_DENIED: Not retryable.
"""


class AppError(Exception):
    pass


class EntityNotFound(AppError):
    pass


class ValidationError(AppError):
    pass


class WorkflowExecutionError(AppError):
    pass


# --- DAG Errors ---


class CycleDetectedError(ValidationError):
    pass


class UnreachableNodesError(ValidationError):
    pass


class InvalidEdgeError(ValidationError):
    pass


class InvalidReferenceError(ValidationError):
    pass


class InvalidTriggerError(ValidationError):
    pass


class LoopBodyEscapeError(ValidationError):
    pass


class InvalidConditionOutputsError(ValidationError):
    pass


class MissingWorkflowIdError(ValidationError):
    pass


class SelfReferencingWorkflowError(ValidationError):
    pass


class CrossWorkflowCycleError(ValidationError):
    pass


# --- Node Handling Errors ---


class UnsupportedNodeTypeError(WorkflowExecutionError):
    pass


class ExpressionError(WorkflowExecutionError):
    """Raised when a {{$expression}} cannot be resolved."""
    pass


class InputRequirementError(WorkflowExecutionError):
    """Raised when required input keys are missing from resolved input."""
    pass


class MaxDepthExceededError(WorkflowExecutionError):
    pass


class LoopCollectionTypeError(WorkflowExecutionError):
    """A LOOP's ``collection`` resolved to a value that is not an array.

    SA-1977 — this used to be a WARNING log and an empty items list, so the run
    went **green having done nothing**: the loop body never ran, the loop node
    was marked completed, and the only trace was a log line nobody reads. A
    workflow that silently skips its work is worse than one that fails.

    Carries the node id and the DECLARED kind, so the message names the actual
    mistake ("this node produced an object; a LOOP needs an array") instead of
    surfacing as ``KeyError: 0``.
    """

    def __init__(self, node_id: str, kind: str) -> None:
        self.node_id = node_id
        self.kind = kind
        super().__init__(
            f"LOOP node {node_id!r}: its 'collection' resolved to a value of "
            f"kind {kind!r}, not an array. A LOOP iterates a collection — point "
            f"it at a node output that is a list, or at a field inside this "
            f"value that holds one."
        )


# --- State Machine Errors ---


class InvalidStateTransitionError(WorkflowExecutionError):
    pass


class IdempotencyError(WorkflowExecutionError):
    """Raised when a duplicate idempotency key is detected."""
    pass


# --- Worker / Infrastructure Errors ---


class WorkerTimeoutError(WorkflowExecutionError):
    retryable: bool = True


class WorkerError(WorkflowExecutionError):
    retryable: bool = True


class InfrastructureError(AppError):
    retryable: bool = True


class PermissionDeniedError(AppError):
    retryable: bool = False


# --- Credential vault verdicts ---
#
# Defined here, not in the vault HTTP client, because two application-layer
# callers have to catch them: ``EventTriggerConsumer`` (to acknowledge a
# doomed event instead of redelivering it forever) and
# ``ChildWorkflowCoordinator`` (to fail the WORKFLOW node instead of the run).
# The client still raises them and re-exports the names from its own module,
# so every existing import keeps working.
#
# Both are VERDICTS: the same answer on every retry. ``retryable = False``
# says so explicitly, which is the distinction that mattered on tenant-1 —
# treating a 403 as transient drove a redelivery loop and a 719ms event-loop
# lag peak with no run to carry the error (SA-2280).


class CredentialNotFound(AppError):
    """The vault has no resolvable (non-revoked) credential for this id."""

    retryable: bool = False


class CredentialResolveAuthError(AppError):
    """The vault refused this caller for this credential.

    Includes the user-less case the automation grant governs: an event- or
    schedule-triggered run has no person behind it, so a user-owned credential
    resolves only when its owner turned Automation on for it (SA-2279).
    """

    retryable: bool = False


# --- NodeDefinition Inheritance Errors ---


class CircularInheritanceError(ValidationError):
    pass


class InheritanceDepthExceededError(ValidationError):
    pass


class WorkflowSnapshotMissingError(AppError):
    """Raised when a run has no workflow snapshot reference (or the
    referenced snapshot row is missing).

    Phase A of the workflow-snapshot rollout fails hard on this rather
    than degrading to a live ``workflow_repo`` read — there is no safe
    way to resume an in-flight run whose snapshot is unavailable.
    """

    def __init__(self, run_id: str, snapshot_id: str = "") -> None:
        suffix = f" (snapshot_id={snapshot_id})" if snapshot_id else ""
        super().__init__(
            f"WorkflowRun {run_id} has no resolvable workflow snapshot{suffix}"
        )
        self.run_id = run_id
        self.snapshot_id = snapshot_id
