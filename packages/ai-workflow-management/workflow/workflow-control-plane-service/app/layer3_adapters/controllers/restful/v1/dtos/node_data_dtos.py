"""Pydantic DTOs for the node-data API surface (Units C / D / E / F).

Split out of ``run_controller.py`` so OpenAPI generation has one canonical
location to pick up the ``response_model`` types, and so FE teams can import
the types directly without loading the whole controller module.

Three groups:

1. **Request DTOs** — validated-on-the-wire payloads.
   - ``PatchNodeDataRequest`` + the flat ``RowOperation`` row-level shape.
   - ``RerunRunRequestBody``, ``ResolvePathsRequestBody``.

2. **Response DTOs** — typed success-shape for every endpoint. These are the
   arguments to ``ApiEnvelope[T]`` when declaring ``response_model=``.

3. **Shared types** — reused ``Literal`` aliases and constrained strings.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

Side = Literal["input", "output", "both"]
PathKind = Literal["scalar", "file_ref", "artifact", "artifact_row", "artifact_column"]
NodeKind = Literal["INPUT", "HUMAN_ACTION", "DATA_EDIT", "FILE_UPLOAD"]
SchemaSource = Literal["design_time", "runtime"]
RerunStrategy = Literal["none", "node_only", "cascade"]

Id = Annotated[str, StringConstraints(min_length=1, max_length=256)]


# ---------------------------------------------------------------------------
# Request: PATCH /data — flat row-level op shape
# ---------------------------------------------------------------------------

RowOpType = Literal[
    "insert", "edit", "delete",
    "add_column", "drop_column", "rename_column",
]


class RowOperation(BaseModel):
    """A single mutation against a CSV artifact.

    Row-level ops:
    - ``insert``: ``row_id`` is ignored / null; backend assigns ``__row_id``
      for array artifacts and upserts row 1 for object artifacts.
    - ``edit``: for array artifacts, ``row_id`` identifies the target row
      and ``data`` is the full replacement row. For object artifacts (single
      row), ``row_id`` is ignored and ``edit`` upserts the row in place,
      same as ``insert``.
    - ``delete``: array artifacts only — ``row_id`` identifies the target
      row; ``data`` is ignored. Rejected on object artifacts.

    Column-level ops (array artifacts only; rejected on ``__root__``):
    - ``add_column``: ``column`` is the new name; ``default_value`` backfills
      every existing row.
    - ``drop_column``: ``column`` is the name to remove. Reserved columns
      (``__row_id``, ``__parent_row_id``) are rejected.
    - ``rename_column``: ``column`` is the old name; ``new_name`` is the
      replacement. Reserved columns are rejected on either side.
    """
    model_config = ConfigDict(extra="forbid")
    operation: RowOpType
    # row-op fields
    row_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    # column-op fields
    column: Optional[str] = None
    new_name: Optional[str] = None
    default_value: Optional[Any] = None


class ArtifactPatch(BaseModel):
    """A batch of ``operations`` targeting a single ``artifact_id``."""
    model_config = ConfigDict(extra="forbid")
    artifact_id: Id
    operations: list[RowOperation] = Field(min_length=1, max_length=1000)


class PatchNodeDataRequest(BaseModel):
    """Body of ``PATCH /runs/{run_id}/nodes/{node_id}/data``.

    The request carries a list of ``artifacts`` — each with its own
    ``artifact_id`` and ``operations``. All operations across all
    artifacts are applied **atomically in order**; any failure rolls the
    whole batch back and newly-uploaded file_ids are orphan-GC'd.

    An empty ``artifacts: []`` is accepted as a no-op: the current
    working copy is unchanged, no event is emitted, and the response
    returns the current ``etag`` / ``version``.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "artifacts": [
                        {
                            "artifact_id": "__root__",
                            "operations": [
                                {
                                    "operation": "insert",
                                    "row_id": None,
                                    "data": {"input": "hello", "input_1": "world"},
                                },
                            ],
                        },
                    ],
                },
                {
                    "artifacts": [
                        {
                            "artifact_id": "__root__",
                            "operations": [
                                {"operation": "insert", "row_id": None,
                                 "data": {"input": "hello"}},
                            ],
                        },
                        {
                            "artifact_id": "table_3_lg2h0",
                            "operations": [
                                {"operation": "insert", "row_id": None,
                                 "data": {"input_2": "alpha", "input_3": "100"}},
                                {"operation": "edit", "row_id": "r-42",
                                 "data": {"input_2": "beta", "input_3": "200"}},
                                {"operation": "delete", "row_id": "r-17"},
                            ],
                        },
                    ],
                },
                {
                    "artifacts": [
                        {
                            "artifact_id": "table_data",
                            "operations": [
                                {"operation": "add_column", "column": "region",
                                 "default_value": "US"},
                                {"operation": "rename_column",
                                 "column": "qty", "new_name": "quantity"},
                                {"operation": "drop_column", "column": "notes"},
                            ],
                        },
                    ],
                },
            ],
        },
    )
    artifacts: list[ArtifactPatch] = Field(min_length=0, max_length=50)


# ---------------------------------------------------------------------------
# Request: POST /rerun and POST /resolve
# ---------------------------------------------------------------------------

class RerunRunRequestBody(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"from_node_id": "n_scoring", "rerun_strategy": "cascade"},
                {"variables": {"threshold": 0.7}},
            ],
        },
    )
    from_node_id: Optional[str] = None
    rerun_strategy: Optional[RerunStrategy] = "cascade"
    variables: Optional[dict[str, Any]] = None


class ResolvePathsRequestBody(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "paths": [
                        "{{$review_node.customer_name}}",
                        "{{$review_node.orders[0].line_items[0].price}}",
                        "{{$artifacts.invoice_pdf.filename}}",
                    ],
                },
            ],
        },
    )
    paths: list[str] = Field(min_length=1, max_length=100)
    task_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models — success shapes for ApiEnvelope[T]
# ---------------------------------------------------------------------------

class NodeInfo(BaseModel):
    """Design-time node metadata — same contract as the ``_node`` block
    enriched onto centralized event envelopes."""
    name: str = ""
    node_type: str = ""
    description: str = ""
    worker_type: str = ""


class GetNodeSchemaResponse(BaseModel):
    """Body of ``GET /runs/{run_id}/nodes/{node_id}/schema``."""
    # The wire/OpenAPI key MUST stay ``schema`` (public FE auto-form contract),
    # but a Python field literally named ``schema`` shadows ``BaseModel.schema``
    # and warns at class-creation. Hold it as ``schema_`` and alias to
    # ``schema`` — OpenAPI advertises ``schema`` (by_alias) and the runtime body
    # is unaffected (ResponseWrapperMiddleware wraps the use-case's raw
    # ``dataclasses.asdict`` dict; this model is OpenAPI-typing only).
    # ``protected_namespaces=()`` keeps the ``schema_version`` / ``schema_source``
    # fields warning-free too.
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)
    schema_: Any = Field(
        ..., alias="schema",
        description="Opaque FE-owned auto-form schema blob.",
    )
    schema_version: str
    schema_source: SchemaSource
    node_kind: str
    node_state: str
    node: NodeInfo = Field(
        default_factory=NodeInfo,
        description="Design-time node metadata (name / node_type / description / worker_type).",
    )


class ArtifactDescriptor(BaseModel):
    artifact_id: str
    file_id: str
    columns: list[str]
    parent_row_key_column: Optional[str] = None
    children: list["ArtifactDescriptor"] = Field(default_factory=list)
    artifact_type: Optional[
        Literal["array_field", "nested_array_field", "object_field"]
    ] = None
    content_type: Optional[str] = None
    # First N preview rows from the descriptor's CSV (default N=5). Populated
    # by GetNodeDataUseCase post-walk when a file client is wired. Empty for
    # descriptors without a file_id (e.g. DATA_EDIT's empty __root__) or when
    # the file service is unreachable.
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)


class NodeDataPayload(BaseModel):
    etag: str
    version: int = Field(ge=0)
    root: Optional[ArtifactDescriptor] = None
    # Populated on the ``output`` side only. ``output_port`` is the port the
    # last ``NodeOutputProduced`` emission took — "success"/"failure" for
    # TASK, branch id for SWITCH, etc. ``taken_edge_ports`` is the
    # {edge_id: port_id} map for CONDITION/SWITCH nodes; empty for others.
    output_port: Optional[str] = None
    taken_edge_ports: Optional[dict[str, str]] = None


class GetNodeDataResponse(BaseModel):
    """Body of ``GET /runs/{run_id}/nodes/{node_id}/data``."""
    run_id: str
    node_id: str
    task_id: str
    node_kind: str
    node_state: str
    schema_ref: dict[str, str]
    data: Optional[NodeDataPayload] = None
    output: Optional[NodeDataPayload] = None
    produced_artifacts: list[str] = Field(default_factory=list)
    etag: str
    # Populated when ``node_state == "FAILED"`` — ``{"message", "error_code"}``.
    # Surfaces the failure reason for handler/expression errors (which write no
    # output ref) as well as worker failures.
    node_error: Optional[dict[str, Any]] = None


class OpResult(BaseModel):
    artifact_id: str
    op_index: int = Field(ge=0)
    status: Literal["ok", "failed"]
    rows_affected: Optional[int] = None
    row_ids: Optional[list[str]] = None
    error: Optional[dict[str, Any]] = None


class PatchNodeDataResponse(BaseModel):
    """Body of ``PATCH /runs/{run_id}/nodes/{node_id}/data``."""
    etag: str
    version: int = Field(ge=0)
    file_lineage: dict[str, str] = Field(default_factory=dict)
    results: list[OpResult] = Field(default_factory=list)
    # True when the FE's PATCH was a "cancel pending node" signal
    # (``data._action`` ∈ {reject, cancel} on a node whose schema does
    # not declare ``_action``). When set, the working copy is unchanged,
    # the task is moved to FAILED with ``error="user_cancelled"``, and
    # the engine routes via failure edges.
    cancelled: bool = False


class SubmitNodeResponse(BaseModel):
    task_id: str
    node_state: str
    downstream_dispatched: bool


class ArtifactRef(BaseModel):
    file_id: str
    filename: Optional[str] = None
    content_type: str
    size_bytes: int = Field(ge=0)
    produced_by_node: str
    produced_by_task: str
    registered_at: str


class GetRunArtifactsResponse(BaseModel):
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    etag: str


class RerunRunResponse(BaseModel):
    reset_node_ids: list[str] = Field(default_factory=list)
    dispatched_task_ids: list[str] = Field(default_factory=list)


class ResolveResult(BaseModel):
    path: str
    status: Literal["ok", "error"]
    kind: Optional[PathKind] = None
    value: Any = None
    error: Optional[dict[str, Any]] = None


class ResolvePathsResponse(BaseModel):
    results: list[ResolveResult]


class ScopedODataResponse(BaseModel):
    """Pass-through body from ``GET /artifacts/{aid}/odata``.

    Declared as a permissive ``dict[str, Any]`` — the file service owns the
    exact shape (``@odata.count``, ``value``, etc.). This class exists to
    give the OpenAPI spec a typed ``response_model`` rather than ``Any``.
    """
    model_config = ConfigDict(extra="allow")


