"""Centralized error codes for all use cases.

Each ErrorCode member carries a machine-readable code string, an HTTP status,
and a default human-readable message.  Use cases set all three fields on their
Result dataclass so controllers can map errors without string matching.

Usage in use cases::

    from app.layer2_application.errors import ErrorCode

    return SomeResult(
        error=ErrorCode.RUN_NOT_FOUND.default_message,
        error_code=ErrorCode.RUN_NOT_FOUND.code,
        error_details={"run_id": run_id},
    )
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(Enum):

    def __init__(self, code: str, http_status: int, default_message: str) -> None:
        self.code = code
        self.http_status = http_status
        self.default_message = default_message

    # ── Not Found (404) ──────────────────────────────────────────────
    RUN_NOT_FOUND = ("RUN_NOT_FOUND", 404, "Run not found")
    WORKFLOW_NOT_FOUND = ("WORKFLOW_NOT_FOUND", 404, "Workflow not found")
    NODE_NOT_FOUND = ("NODE_NOT_FOUND", 404, "Node not found")
    TASK_NOT_FOUND = ("TASK_NOT_FOUND", 404, "Task not found")
    WEBHOOK_NOT_FOUND = ("WEBHOOK_NOT_FOUND", 404, "Webhook not found")
    NODE_DEFINITION_NOT_FOUND = ("NODE_DEFINITION_NOT_FOUND", 404, "Node definition not found")
    PARENT_DEFINITION_NOT_FOUND = ("PARENT_DEFINITION_NOT_FOUND", 404, "Parent definition not found")
    ARTIFACT_NOT_FOUND = ("ARTIFACT_NOT_FOUND", 404, "Artifact not found")
    GATEWAY_APP_NOT_FOUND = ("GATEWAY_APP_NOT_FOUND", 404, "Gateway app not found")
    GATEWAY_FUNCTION_NOT_FOUND = ("GATEWAY_FUNCTION_NOT_FOUND", 404, "Gateway function not found")
    ENVIRONMENT_NOT_FOUND = ("ENVIRONMENT_NOT_FOUND", 404, "Environment not found")
    ENVIRONMENT_ALREADY_EXISTS = ("ENVIRONMENT_ALREADY_EXISTS", 409, "An environment with the same section and name already exists")
    # SA-2471 — section-level shared variable key schema.
    SECTION_SCHEMA_NOT_FOUND = ("SECTION_SCHEMA_NOT_FOUND", 404, "Section schema not found")
    SECTION_SCHEMA_ALREADY_EXISTS = ("SECTION_SCHEMA_ALREADY_EXISTS", 409, "A section schema already exists for this section")
    SECTION_SCHEMA_INVALID_KEYS = ("SECTION_SCHEMA_INVALID_KEYS", 422, "Section schema keys are invalid (duplicate, empty, or malformed)")
    ENVIRONMENT_VARIABLES_INVALID = ("ENVIRONMENT_VARIABLES_INVALID", 422, "Environment variables do not match the section schema")
    NOT_FOUND = ("NOT_FOUND", 404, "Resource not found")

    # ── Validation (400) ─────────────────────────────────────────────
    VALIDATION_ERROR = ("VALIDATION_ERROR", 400, "Validation error")
    # SA-2450 — workflow↔section binding. A save with no section / zero
    # environments / an env not in the chosen section is unprocessable (422);
    # a run whose environment_id is not in the workflow's assigned subset (or
    # any env for an unassigned workflow) is likewise rejected (422).
    WORKFLOW_ENVIRONMENT_INVALID = ("WORKFLOW_ENVIRONMENT_INVALID", 422, "Invalid workflow environment assignment")
    ENVIRONMENT_NOT_ASSIGNED = ("ENVIRONMENT_NOT_ASSIGNED", 422, "The selected environment is not assigned to this workflow")
    # SA-2605 — section-only binding: the run-time environment (static or ⚡ dynamic)
    # must be a member of the workflow's bound section, else the run is hard-blocked.
    ENVIRONMENT_NOT_IN_SECTION = ("ENVIRONMENT_NOT_IN_SECTION", 422, "The selected environment is not in the workflow's section")
    # SA-2605/SA-2613 — an environment in several sections must see each key with a
    # consistent type. Adding it to a section whose schema types a key differently
    # from another of its sections is rejected.
    KEY_TYPE_CONFLICT = ("KEY_TYPE_CONFLICT", 422, "A variable key has conflicting types across this environment's sections")
    # SA-2496 — "Dynamic environment (only id)" on the call-subflow node. The
    # expression must resolve to a concrete environment id; a blank/unresolvable
    # value is an error (never a silent fall back to "no environment").
    DYNAMIC_ENV_UNRESOLVED = ("DYNAMIC_ENV_UNRESOLVED", 422, "The dynamic environment expression did not resolve to an environment id")
    INVALID_NODE_DATA = ("INVALID_NODE_DATA", 400, "Invalid node data")
    INVALID_EDGE_DATA = ("INVALID_EDGE_DATA", 400, "Invalid edge data")
    INVALID_EDGE_REFERENCE = ("INVALID_EDGE_REFERENCE", 400, "Edge references unknown node")
    NODE_CONFIG_INVALID = ("NODE_CONFIG_INVALID", 400, "Node configuration invalid")
    INVALID_WORKFLOW = ("INVALID_WORKFLOW", 400, "Invalid workflow")
    INPUT_VALIDATION_FAILED = ("INPUT_VALIDATION_FAILED", 400, "Input validation failed")
    NOTHING_TO_EDIT = ("NOTHING_TO_EDIT", 400, "Nothing to edit")
    # PATCH /data column-op validation
    RESERVED_COLUMN = ("RESERVED_COLUMN", 400, "Reserved column cannot be added/dropped/renamed")
    DUPLICATE_COLUMN = ("DUPLICATE_COLUMN", 400, "Column with that name already exists")
    UNKNOWN_COLUMN = ("UNKNOWN_COLUMN", 400, "Column not found in artifact")
    INVALID_COLUMN = ("INVALID_COLUMN", 400, "Column field missing or empty")
    OBJECT_OP_NOT_SUPPORTED = ("OBJECT_OP_NOT_SUPPORTED", 400, "Operation not supported on object artifact")
    OP_UNKNOWN = ("OP_UNKNOWN", 400, "Unknown operation kind")
    ROW_ID_REQUIRED = ("ROW_ID_REQUIRED", 400, "row_id is required for this operation")
    EMPTY_ARTIFACTS = ("EMPTY_ARTIFACTS", 400, "artifacts list must contain at least one entry")

    # ── State Conflict (409) ─────────────────────────────────────────
    INVALID_RUN_STATUS = ("INVALID_RUN_STATUS", 409, "Operation not allowed in current run status")
    INVALID_TASK_STATUS = ("INVALID_TASK_STATUS", 409, "Operation not allowed in current task status")
    CANNOT_DELETE_IN_USE = ("CANNOT_DELETE_IN_USE", 409, "Cannot delete: resource is in use")
    PRECONDITION_REQUIRED = ("PRECONDITION_REQUIRED", 409, "If-Match header is required on this endpoint")
    WORKFLOW_VERSION_CONFLICT = (
        "WORKFLOW_VERSION_CONFLICT", 409,
        "Workflow has been modified by another process. Do you want to create new version?",
    )
    # G-guard: callback refers to a task whose run_generation is older
    # than the run's current generation — it came from a superseded
    # execution (e.g. test-run was reset and re-executed). The worker
    # result is dropped; worker-executor treats this as terminal.
    TASK_SUPERSEDED = (
        "TASK_SUPERSEDED", 409,
        "Task belongs to a superseded execution generation; result dropped.",
    )
    GATEWAY_APP_NAME_TAKEN = (
        "GATEWAY_APP_NAME_TAKEN", 409,
        "A gateway app with this name already exists.",
    )
    GATEWAY_FUNCTION_NAME_TAKEN = (
        "GATEWAY_FUNCTION_NAME_TAKEN", 409,
        "A function with this name already exists on this app.",
    )
    WORKFLOW_ALREADY_PUBLISHED = (
        "WORKFLOW_ALREADY_PUBLISHED", 409,
        "Workflow already has a published version. Confirm to overwrite.",
    )

    # ── Concurrency (412) / Gone (410) ───────────────────────────────
    PRECONDITION_FAILED = ("PRECONDITION_FAILED", 412, "ETag mismatch (stale If-Match)")
    INPUT_EVICTED = ("INPUT_EVICTED", 410, "Input files are no longer resolvable")
    # Phase M.2.1: raised by IRunRepository.save when another caller's save
    # landed between this caller's load and save (run.version ≠ persisted VERSION).
    OPTIMISTIC_CONCURRENCY_CONFLICT = (
        "OPTIMISTIC_CONCURRENCY_CONFLICT", 409,
        "Run was modified by another request; reload and retry.",
    )

    # ── Relationship (400) ───────────────────────────────────────────
    TASK_RUN_MISMATCH = ("TASK_RUN_MISMATCH", 400, "Task does not belong to this run")
    TENANT_MISMATCH = ("TENANT_MISMATCH", 400, "Resource must belong to the same tenant")
    NO_HANDLER = ("NO_HANDLER", 400, "No handler registered for node type")

    # ── Node testability (422) ───────────────────────────────────────
    NODE_NOT_TESTABLE_STANDALONE = (
        "NODE_NOT_TESTABLE_STANDALONE", 422,
        "This node type can only be tested on the workflow canvas.",
    )

    # ── Configuration (503) ──────────────────────────────────────────
    SERVICE_NOT_CONFIGURED = ("SERVICE_NOT_CONFIGURED", 503, "Required service not configured")

    # ── Gateway OpenAPI sync ─────────────────────────────────────────
    GATEWAY_SPEC_FETCH_FAILED = ("GATEWAY_SPEC_FETCH_FAILED", 502, "Failed to fetch the OpenAPI spec")
    GATEWAY_SPEC_INVALID = ("GATEWAY_SPEC_INVALID", 422, "OpenAPI spec is invalid or unparseable")

    # ── Data / File Operations (422) ─────────────────────────────────
    DATA_OPERATION_FAILED = ("DATA_OPERATION_FAILED", 422, "Data operation failed")
    FILE_OPERATION_FAILED = ("FILE_OPERATION_FAILED", 422, "File operation failed")
    ENRICHMENT_FAILED = ("ENRICHMENT_FAILED", 422, "Node definition enrichment failed")

    # ── Node-data PATCH (422) ────────────────────────────────────────
    VALIDATION_FAILED = ("VALIDATION_FAILED", 422, "Validation failed for one or more ops")
    STATIC_SCHEMA_IMMUTABLE = ("STATIC_SCHEMA_IMMUTABLE", 422, "Column op requires a runtime (DATA_EDIT) schema")
    ROW_PATH_AMBIGUOUS = ("ROW_PATH_AMBIGUOUS", 422, "row_path or filter is ambiguous/empty")
    NO_INPUT_REFERENCE = ("NO_INPUT_REFERENCE", 422, "Node has no input/output separation — reset not possible")
    ROW_NOT_FOUND = ("ROW_NOT_FOUND", 422, "row_path resolved no row under the given filter")
    MISSING_SYNTHETIC_COLUMNS = ("MISSING_SYNTHETIC_COLUMNS", 422, "Replacement file is missing __row_id / __parent_row_id")
    COLUMN_MISMATCH = ("COLUMN_MISMATCH", 422, "Replacement columns do not match artifact columns")
    PARENT_ROW_ID_MISMATCH = ("PARENT_ROW_ID_MISMATCH", 422, "Nested replacement rows reference unexpected __parent_row_id")
    FILE_NOT_FOUND = ("FILE_NOT_FOUND", 422, "file_id not found in file service")

    # ── Generic ──────────────────────────────────────────────────────
    BAD_REQUEST = ("BAD_REQUEST", 400, "Bad request")
    INTERNAL_ERROR = ("INTERNAL_ERROR", 500, "Internal error")

    # ── Lookup ───────────────────────────────────────────────────────

    @classmethod
    def from_code(cls, code: str) -> ErrorCode | None:
        """Return the ErrorCode member matching *code*, or ``None``."""
        for member in cls:
            if member.code == code:
                return member
        return None
