# Node Input Review & Edit — Implementation Plan

> Implements the API design described in `node-input-review-edit-api.md` and `node-input-api-reference.md`.
> Service: `workflow-control-plane-service`
> Base path: `workflow-control-plane-service/app/`

---

## Decisions Log

Locked-in answers to pre-implementation questions. Changes to these require re-planning.

| # | Question | Decision |
|---|---|---|
| 1 | Pydantic version | **v2** — `Annotated`, `Field(discriminator=...)`, `model_validator`, `StringConstraints`, `ConfigDict(extra="forbid")`. |
| 2 | Row-id library | **`uuid7`** package. Monotonic, time-ordered. Injected into every CSV row at write time on `__row_id`. |
| 3 | `rerun_strategy != "none"` on a PENDING node | **Silently degraded to `"none"`**. PATCH still commits; response carries `rerun.degraded_from = <original>` so FE can toast. |
| 4 | `reset_artifact` after `drift_ok` `replace_artifact` | **Schema reverts to match input.** When we revert the artifact's `file_id` to the input's, we also rebuild its runtime-schema entry from the input file's columns. Both data ETag and schema ETag bump. |
| 5 | Old `resume_pending_task` split | **Clean extraction.** `_resume_human_interaction_task()` (lines 199–433 of `resume_pending_task_use_case.py`) is uncoupled from worker-completion; lift it into `submit_node`. `resume_pending_task` stays for worker-completion only; its public controller route is deleted (merged into `POST /nodes/{id}/submit?task_id=...`). |
| 6 | Error responses | **Use existing `AppError` pattern**, NOT `HTTPException`. `app_error_handler` (registered in `create_fastapi_app()`) produces the envelope response. Middleware skips pre-wrapped envelopes. Extend `AppError` with every new code from §11 of the design doc. |

---

## Phase 1: Data Model Foundation

Changes to entities and helpers that everything else depends on. Must land first.

### 1.1 Recursive `__file_ref` + `$artifacts` registry shape

**File: `layer1_domain/entities/data_reference.py`**

| Change | Detail |
|---|---|
| `build_file_ref()` | Add params: `version: int = 0`, `etag: str = ""`, `artifact_id: str = "__root__"`. Include them in the returned dict. |
| New: `walk_file_ref(ref, visitor)` | DFS traversal. `visitor(artifact_dict, depth)` called for each level. Returns nothing. |
| New: `find_artifact(ref, artifact_id) -> dict \| None` | Walk the tree; return the first artifact matching dotted-path `artifact_id`. |
| `add_artifact()` | Add params: `artifact_id: str = ""`, `parent_row_key_column: str \| None = None`. Include `artifacts: list = []` on each artifact (recursive). |
| `register_artifacts()` | **Signature**: `register_artifacts(variables, node_output, *, node_id: str = "", task_id: str = "")`. Store metadata object per name instead of bare `file_id`. |
| Remove | No flat-artifact-only helpers remain; everything is recursive. |

New `register_artifacts` body:

```python
def register_artifacts(
    variables: dict[str, Any],
    node_output: Any,
    *,
    node_id: str = "",
    task_id: str = "",
) -> None:
    if not is_file_ref(node_output):
        return
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for art in node_output.get("artifacts", []):
        name = art.get("name", "")
        fid = art.get("file_id", "")
        if name and fid:
            variables.setdefault("artifacts", {})[name] = {
                "file_id": fid,
                "filename": art.get("filename"),
                "content_type": art.get("content_type", ""),
                "size_bytes": art.get("size_bytes", 0),
                "produced_by_node": node_id,
                "produced_by_task": task_id,
                "registered_at": now,
            }
```

### 1.2 Update all `register_artifacts` call sites

Each caller must now pass `node_id` and `task_id`:

| File | Line | Change |
|---|---|---|
| `layer2_application/features/complete_task/use_cases/complete_task_use_case.py` | ~186 | `register_artifacts(run.context.variables, task_output, node_id=task.node_id, task_id=task.id)` |
| `layer2_application/features/resume_pending_task/use_cases/resume_pending_task_use_case.py` | ~354 | `register_artifacts(run.context.variables, node_output, node_id=task.node_id, task_id=task.id)` |
| `layer2_application/features/add_node_artifact/use_cases/add_node_artifact_use_case.py` | ~82 | `register_artifacts(run.context.variables, ctx_value, node_id=cmd.node_id, task_id=cmd.task_id)` |

### 1.3 Fix `data_edit_handler.py` fallback

**File: `layer1_domain/services/node_handlers/data_edit_handler.py` lines 67-80**

The `_recover_with_artifact_lookup()` path reads `$artifacts.<name>` values. Since the shape is now an object, any code that does `file_id = artifacts_dict[name]` must become `file_id = artifacts_dict[name]["file_id"]`. Search for all dict reads of the artifacts map inside this handler and adjust.

### 1.4 Two ETags: node data vs schema

Each node maintains **two independent ETags**, stored on the `__file_ref` root and on `task.output_metadata` respectively:

| ETag | Key | Computed over | Bumps on |
|---|---|---|---|
| **Node data ETag** | `__file_ref.etag` | `main_file_id + sorted(descendant file_ids) + revision` | Any data mutation (PATCH, reset) |
| **Schema ETag** | `task.output_metadata["schema_etag"]` | `sha256(schema_version + json.dumps(schema, sort_keys=True))` | Schema regeneration (DATA_EDIT handler) + column ops + `replace_artifact` under `drift_ok` |

`If-Match` on `PATCH /data` always refers to the **node data ETag**. `If-None-Match` on `GET /schema` refers to the schema ETag. A data-only edit does not bump schema ETag; a column op bumps both.

### 1.5 Audit existing `$artifacts` variable expressions

```bash
grep -r '\$artifacts\.' --include='*.py' --include='*.json' app/ tests/
```

Every match that expects a bare string must be rewritten to append `.file_id`. Document the affected files.

---

## Phase 2: Domain Services (new, `layer1_domain/services/`)

### 2.1 `node_input_etag_calculator.py`

```python
class NodeInputETagCalculator:
    def compute(self, file_ref: dict, schema_version: str) -> str:
        """sha256(main_file_id | sorted(descendant_file_ids) | schema_version | revision)"""
```

- `walk_file_ref` to collect all `file_id`s.
- Return `"sha256:" + hashlib.sha256(payload).hexdigest()`.
- Used by `GetNodeDataUseCase` and `EditNodeDataUseCase`.

### 2.2 `row_path_resolver.py`

```python
class RowPathResolver:
    def __init__(self, file_service_client, logger):
        ...

    async def resolve(self, file_ref: dict, row_path: list[dict]) -> list[str]:
        """Walk row_path root→leaf, resolve each hop's row_index+filter → __row_id.
        Returns list of resolved __row_ids (one per hop).
        Raises RowNotFoundError if any hop fails.
        """
```

Per hop:
1. Find artifact in tree via `find_artifact(file_ref, hop["artifact_id"])`.
2. Build OData query: `hop["filter"] + "&$skip={hop['row_index']}&$top=1&$select=__row_id"`.
3. For nested hops (depth > 0): prepend `$filter=__parent_row_id eq '{prev_resolved_row_id}'` to the filter.
4. Call `file_service_client.query_odata(file_id, query)` → extract `__row_id` from the single row.
5. If no row returned → `RowNotFoundError(hop_index=i)`.

Dependencies: `file_service_client` (already in `available_dependencies` as `file_service_client`).

### 2.3 `node_input_validator.py`

```python
class NodeInputValidator:
    # Value validation (rule dispatcher)
    def validate_scalars(self, schema: dict, scalars: dict) -> list[ValidationOpError]: ...
    def validate_cell(self, schema: dict, artifact_id: str, column: str, value: Any) -> list[ValidationOpError]: ...
    def validate_rows(self, schema: dict, artifact_id: str, rows: list[dict]) -> list[ValidationOpError]: ...
    def validate_assignments(self, schema: dict, artifact_id: str, assignments: dict) -> list[ValidationOpError]: ...
    def validate_full_tree(self, schema: dict, file_ref: dict) -> list[ValidationOpError]: ...

    # Structural validation (new — for column ops)
    def validate_add_column(self, artifact: dict, column: str, output_type: str) -> list[ValidationOpError]: ...
    def validate_drop_column(self, artifact: dict, column: str) -> list[ValidationOpError]: ...
    def validate_rename_column(self, artifact: dict, old_name: str, new_name: str) -> list[ValidationOpError]: ...

    # External-file validation (new — for replace_artifact)
    async def validate_replace_artifact(
        self,
        artifact: dict,                          # target artifact node in __file_ref
        replacement_file_id: str,
        replacement_columns: list[str],          # from file-service HEAD/metadata
        schema_compatibility: str,               # "strict" | "drift_ok"
        expected_columns: list[str] | None,
        is_nested: bool,
        expected_parent_row_id: str | None,      # when is_nested
        replacement_parent_row_ids: list[str],   # from file-service sample query
    ) -> list[ValidationOpError]:
        """Check synthetic columns, column compatibility, parent_row_id consistency."""
```

- **Rule dispatcher**: reads `rules: [{method, value, message}]` from each schema field. Dispatches on `method` string to evaluator functions. Unknown method → error.
- **Column-op validators** enforce:
  - `duplicate_column` — `new_name` (or `column` for add) must not already exist on the artifact.
  - `reserved_column` — `__row_id` / `__parent_row_id` cannot be added, dropped, or renamed.
  - `output_type` must be `"string" | "number" | "boolean"`.
- **`replace_artifact` validator** enforces:
  - `MISSING_SYNTHETIC_COLUMNS` — `__row_id` must be in replacement's columns; `__parent_row_id` required for nested.
  - `COLUMN_MISMATCH` — under `strict`, replacement columns (minus synthetic) must equal artifact's declared columns (minus synthetic). Under `drift_ok`, any column set is accepted (caller has separately enforced schema mutability).
  - If `expected_columns` provided, replacement's columns must also match it.
  - `PARENT_ROW_ID_MISMATCH` — every `__parent_row_id` in the replacement sample must equal `expected_parent_row_id`.

`STATIC_SCHEMA_IMMUTABLE` is checked by the caller (`EditNodeDataUseCase`) before invoking column-op validators (also for `replace_artifact` under `drift_ok`), since schema source is a node-level property, not a per-op validator concern.

`FILE_NOT_FOUND` is raised by the use case when the file-service HEAD query fails — not by the validator (I/O boundary).

`validate_replace_artifact` is `async` because it consumes file-service metadata / sample-query output provided by the caller. No external dependencies (file-service client is held by the use case, which passes results in).

---

### 2.4 `run_lock_registry.py` (new)

Per-run serialization for read-modify-write PATCHes.

```python
class RunLockRegistry:
    """Per-process registry of asyncio.Lock keyed by run_id.
    Out-of-process consistency still relies on ETag (If-Match)."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._mgmt = asyncio.Lock()

    async def acquire(self, run_id: str) -> asyncio.Lock:
        async with self._mgmt:
            lock = self._locks.get(run_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[run_id] = lock
        return lock

    async def release(self, run_id: str) -> None:
        # Lock is reused across requests; GC'd via LRU when dict grows past cap.
        pass
```

- Used as `async with (await lock_registry.acquire(run_id)):` inside `EditNodeDataUseCase` and `ResolveVariablePathsUseCase` (for consistent snapshots).
- Register as `"run_lock_registry"` in `available_dependencies` (bootstrap.py).

### 2.5 `IFileServiceClient` interface — required methods

The interface `layer2_application/interfaces/file_service_client.py` must expose (add any missing):

```python
class IFileServiceClient(Protocol):
    async def head(self, file_id: str, *, tenant_id: str) -> FileMetadata | None:
        """Return {size_bytes, content_type, columns, row_count} or None if not found."""

    async def query_odata(
        self, file_id: str, query: str, *, tenant_id: str
    ) -> ODataPage:
        """Forward OData query; return {"@odata.count"?, value: [...]}."""

    async def download(self, file_id: str, *, tenant_id: str) -> bytes:
        """Full-file download (CSV bytes) — used by edit ops to read-then-upload."""

    async def upload(self, content: bytes, *, tenant_id: str,
                     content_type: str = "text/csv") -> str:
        """Upload bytes; return new file_id."""

    async def delete(self, file_id: str, *, tenant_id: str) -> None:
        """Best-effort delete — used for orphan GC. Must not raise on 404."""
```

All methods are tenant-scoped. The control-plane resolves tenant_id from the run's metadata before each call.

Already-existing methods are kept as-is; missing ones are added.

## Phase 3: New Use Cases (features)

Each follows the repo convention:
```
features/{name}/
├── __init__.py                # from .use_cases.{name}_use_case import {Name}UseCase
└── use_cases/
    ├── __init__.py
    └── {name}_use_case.py
```

### 3.1 Delete / reshape old features

| Feature | Action | Replaced by |
|---|---|---|
| `features/get_node_input_data/` | **Delete** | `get_node_data` |
| `features/edit_node_input/` | **Delete** | `edit_node_data` |
| `features/resume_input_node/` | **Delete** | `submit_node` |
| `features/resume_pending_task/` | **Keep, slim down** — remove the user-resume branch (`_resume_human_interaction_task()` at lines 199–433 of its use case), leaving only the worker-completion path. The feature stays internal (invoked by `complete_task` or worker flows only); the controller route `POST /runs/{run_id}/tasks/{task_id}/resume` is **deleted** and its responsibilities merge into `POST /nodes/{node_id}/submit?task_id=...`. | — |

**`submit_node` lift strategy**: copy the body of `_resume_human_interaction_task()` into `submit_node_use_case.py` (preserving existing stage-event emission, `task.resolved_input` merge, downstream dispatch via `ResumeTaskAction`). Then delete that method + its call site (line 156) from `resume_pending_task_use_case.py`. Adjust imports.

### 3.2 Create new features

#### `get_node_schema`

```python
class GetNodeSchemaUseCase:
    def __init__(self, logger, run_repo, task_repo, workflow_repo, **kwargs):
        ...
    async def execute(self, cmd: GetNodeSchemaCommand) -> Result:
        # 1. Load run, find node, resolve task (latest or cmd.task_id)
        # 2. Resolve schema (precedence): task.output_metadata["input_schema"]
        #    or node.config["_node_definition"]["input_schema"]
        #    or node.config["input_schema"]
        # 3. Determine schema_source: "runtime" if resolved from task.output_metadata, else "design_time"
        # 4. Compute schema_version (hash of schema JSON)
        # 5. Return GetNodeSchemaResult(schema=schema, schema_version=..., schema_source=..., node_kind=..., node_state=...)
```

#### `get_node_data`

```python
class GetNodeDataUseCase:
    def __init__(self, logger, run_repo, task_repo, workflow_repo,
                 file_service_client=None, **kwargs):
        ...
    async def execute(self, cmd: GetNodeDataCommand) -> Result:
        # 1. Load run, find node, resolve task
        # 2. Resolve data sources:
        #    side=input  → task.resolved_input   (frozen original; DATA_EDIT: upstream data)
        #    side=output → variables[node_id]     (working copy; what PATCH modifies)
        #    side=both   → return both
        #    For INPUT/HUMAN_ACTION: input == output (same ref)
        # 3. Build recursive descriptor tree from file_ref (walk_file_ref)
        # 4. Extract scalars from file_ref["extracted"]
        # 5. Compute ETag via NodeInputETagCalculator
        # 6. Determine produced_artifacts: names in run.context.variables["artifacts"]
        #    where produced_by_node == node_id
        # 7. Return GetNodeDataResult(data=NodeData(...), output=NodeData(...), etag=..., produced_artifacts=[...])
```

#### `edit_node_data`

```python
class EditNodeDataUseCase:
    def __init__(self, logger, run_repo, task_repo, workflow_repo,
                 orchestration_engine=None, file_service_client=None,
                 data_store_service=None, orchestration_queue=None, **kwargs):
        ...
    async def execute(self, cmd: EditNodeDataCommand) -> Result:
        # Full snapshot-and-swap flow:
        # 0. If cmd.reset_to_input: swap variables[node_id] = task.resolved_input,
        #    bump ETag, apply rerun_strategy, return. Skip all ops below.
        # 1. Acquire per-run lock
        # 2. Load working copy (variables[node_id]), compute current ETag, check If-Match
        # 3. Pre-check op types:
        #    - Column ops (add_column / drop_column / rename_column):
        #        reject if schema_source != "runtime" → STATIC_SCHEMA_IMMUTABLE
        #    - replace_artifact with schema_compatibility="drift_ok":
        #        reject if schema_source != "runtime" → STATIC_SCHEMA_IMMUTABLE
        #    - reset_to_input / reset_artifact: reject if node has no input/output separation
        #        → NO_INPUT_REFERENCE
        #    - delete_row: require non-empty row_path with terminal hop → ROW_PATH_AMBIGUOUS
        #    - delete_rows: require non-empty filter → ROW_PATH_AMBIGUOUS
        # 4. For each replace_artifact op:
        #    a. file_service_client.head(file_id) — 404 → FILE_NOT_FOUND
        #    b. Read replacement's columns from HEAD response (or metadata endpoint)
        #    c. If nested: resolve parent row_path → expected_parent_row_id
        #       Sample-query replacement file: $top=N $select=__parent_row_id (N = small, e.g. 256)
        #       (For files > N rows, trust the column-mismatch + tenant-scoped HEAD as sufficient guard;
        #        caller is a trusted external service.)
        #    d. Call NodeInputValidator.validate_replace_artifact(...)
        # 5. Process reset_artifact ops: swap matching artifact file_ids from task.resolved_input.
        #    If the current artifact's columns differ from the input's (drift from a prior
        #    drift_ok replace_artifact), rebuild task.output_metadata["input_schema"]'s
        #    field entries for that artifact from the input file's columns. Bump schema_version.
        # 6. Validate remaining ops via NodeInputValidator
        #    (including column-op specific: duplicate_column, reserved_column, output_type)
        # 7. Resolve row_paths (update_cell, delete_row) via RowPathResolver
        # 8. Per op: download CSV → mutate → upload → journal {old_file_id, new_file_id}
        #    - delete_row: delete single row by resolved __row_id (+ cascade to nested children)
        #    - replace_artifact (root): swap artifact.file_id to replacement's file_id in the tree
        #      (no download/upload — the file is already uploaded by the external service)
        #    - replace_artifact (nested): download current child CSV, strip rows with
        #      __parent_row_id == expected_parent_row_id, append replacement's rows, upload new file_id
        #    - add_column: add header + default value to every row; update task.output_metadata["input_schema"]
        #    - drop_column: remove header + strip values; update runtime schema
        #    - rename_column: atomic header rename + runtime schema field-key rename
        # 9. On any failure: abort, GC orphans (do NOT delete the external replace_artifact file_id —
        #    it wasn't created by us; external service owns its lifecycle), return error
        # 10. Rebuild file_ref tree with new file_ids, bump version, compute new ETag
        #     If any column op or drift_ok replace ran: also bump schema_version and persist updated runtime schema.
        # 8. Swap run.context.variables[node_id] pointer (COMMIT)
        # 9. Dispatch rerun per rerun_strategy
        # 10. Release lock, return response
```

#### `submit_node`

```python
class SubmitNodeUseCase:
    def __init__(self, logger, run_repo, task_repo, workflow_repo,
                 stage_recorder, orchestration_engine=None,
                 data_store_service=None, orchestration_queue=None, **kwargs):
        ...
    async def execute(self, cmd: SubmitNodeCommand) -> Result:
        # 1. Resolve target task (latest or cmd.task_id)
        # 2. Validate full file_ref tree via NodeInputValidator.validate_full_tree
        # 3. Set task.resolved_input = current file_ref
        # 4. Transition node state PENDING → SUBMITTED
        # 5. Dispatch downstream via orchestration engine
        # 6. Return SubmitNodeResult(task_id=..., node_state="SUBMITTED", downstream_dispatched=True)
```

#### `rerun_run`

```python
class RerunRunUseCase:
    def __init__(self, logger, run_repo, task_repo, orchestration_engine=None,
                 orchestration_queue=None, **kwargs):
        ...
    async def execute(self, cmd: RerunRunCommand) -> Result:
        # 1. If from_node_id: collect_downstream_nodes_to_reset(from_node_id, dag_info)
        # 2. Else: collect all nodes
        # 3. Reset task states for affected nodes
        # 4. Override run variables if cmd.variables
        # 5. Dispatch from start node(s) or from_node_id
        # 6. Return RerunRunResult(reset_node_ids=[...], dispatched_task_ids=[...])
```

#### `query_node_artifact`

```python
class QueryNodeArtifactUseCase:
    def __init__(self, logger, run_repo, task_repo, file_service_client=None, **kwargs):
        ...
    async def execute(self, cmd: QueryNodeArtifactCommand) -> Result:
        # 1. Load run, find node, resolve task
        # 2. Verify artifact_id exists in the file_ref tree via find_artifact
        # 3. Forward OData query to file_service_client.query_odata(file_id, query_params)
        # 4. Return raw result (pass-through)
```

#### `validate_node_input`

```python
class ValidateNodeInputUseCase:
    def __init__(self, logger, run_repo, task_repo, workflow_repo, **kwargs):
        ...
    async def execute(self, cmd: ValidateNodeInputCommand) -> Result:
        # Thin wrapper around NodeInputValidator.validate_full_tree
```

#### `get_run_artifacts`

```python
class GetRunArtifactsUseCase:
    def __init__(self, logger, run_repo, **kwargs):
        ...
    async def execute(self, cmd: GetRunArtifactsCommand) -> Result:
        # 1. Load run
        # 2. Read run.context.variables.get("artifacts", {})
        # 3. Return as-is (already metadata-object shaped after Phase 1)
```

#### `resolve_variable_paths`

```python
class ResolveVariablePathsUseCase:
    def __init__(self, logger, run_repo, variable_resolver,
                 file_service_client=None, **kwargs):
        ...
    async def execute(self, cmd: ResolveVariablePathsCommand) -> Result:
        # 1. Load run
        # 2. For each path in cmd.paths:
        #    a. Parse expression (strip {{ }}, split on dots/brackets)
        #    b. Evaluate via variable_resolver.resolve(expression, run.context.variables)
        #    c. For nested-artifact paths with [index], use RowPathResolver
        #    d. Classify result kind (scalar / file_ref / artifact / artifact_row / artifact_column)
        #    e. Capture value or error
        # 3. Return ResolvePathsResult(results=[...])
```

### 3.3 Modify `get_variable_suggestions`

**File: `features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py`**

Changes:

1. **Schema tree walker** (new private method): recurse through upstream node's schema `fields[]`. For each field:
   - Scalar (`outputType ∈ {string, number, boolean}`): emit `{path, kind="scalar", output_type}`.
   - Object with file-upload control: emit `{path, kind="file_ref"}` + expand one level (`.file_id`, `.filename`, `.size_bytes`, `.content_type`).
   - Object without file-upload: emit `{path, kind="scalar"}` + recurse into child fields.
   - Array with `wrapperProps.artifact_binding`: emit `{path, kind="artifact"}` + `{path[*], kind="artifact_row"}` + per-column `{path[*].col, kind="artifact_column"}`. Recurse into nested tables.

2. **`$artifacts` section** (~lines 554-578): iterate the metadata-object registry and emit `{kind="file_ref"}` for each name + auto-expand metadata sub-fields.

3. **Add `kind` to `VariablePath`** result dataclass.

---

## Phase 4: Recursive Storage

### 4.1 `layer1_domain/services/data_store_service.py`

| Change | Detail |
|---|---|
| `store_data()` | When an array-of-dicts row contains further array-of-dicts fields, recurse: split the nested array into a child CSV with `__parent_row_id`. Inject `__row_id` (ULID) into every CSV row. Build recursive `__file_ref` tree. |
| New: `materialize_tree(file_ref)` | DFS over the recursive `__file_ref`; for each artifact, download CSV from file service, join children on `__parent_row_id`, reconstruct the original nested dict structure. Used by the resolver for full materialization. |
| Remove flat path | The existing single-level artifact split is replaced by the recursive version. |

### 4.2 `layer1_domain/services/file_backed_resolver.py`

- `FileBackedResolverStrategy.resolve()`: when traversing a `__file_ref`, walk `artifacts[]` recursively. Support indexed access `[i]` on artifacts (resolve row by index → download CSV → get ith row).

### 4.3 `layer1_domain/services/dag_traversal.py`

New function (sibling of `collect_downstream_nodes_to_skip` at line 10):

```python
def collect_downstream_nodes_to_reset(start_node_id: str, dag_info: DAGInfo) -> set[str]:
    """BFS from start_node_id; collect all downstream nodes for state reset.
    A node is included if reachable from start_node_id via adjacency.
    """
```

### 4.4 Orphan file GC (best-effort, post-PATCH)

When a PATCH succeeds, old `file_id`s (the pre-image set) become orphans — no longer referenced by the node's `__file_ref` tree. They are not deleted synchronously (commit path must stay fast and not fail on file-service latency).

Mechanism:

- On successful commit, the use case enqueues a background task via `asyncio.create_task(_gc_orphans(old_file_ids, tenant_id))` that calls `file_service_client.delete(fid, tenant_id=...)` for each. Any error is logged and swallowed.
- Exception: `replace_artifact` orphans the NODE's old file_id, but the **external** file_id (the replacement) is not orphaned — it's in the tree now. The pre-image set for an op includes only file_ids that existed in the tree before the op.
- On abort (op failure mid-batch), the **newly uploaded** file_ids are orphans. Same GC path, fire-and-forget.
- Document trade-off: if a GC call is lost (process crash), a file leaks. Acceptable — file service has its own retention policy / reaper.
- For `replace_artifact` external file_ids: the external service owns the file pre-commit; post-commit it becomes part of the tree and is GC-managed like any other artifact file on subsequent edits.

No separate service needed — it's a private helper in `EditNodeDataUseCase`.

---

## Phase 5: Controllers + DTOs

### 5.1 Delete controllers

| File to delete | Reason |
|---|---|
| `layer3_adapters/controllers/restful/v1/file_controller.py` | Raw proxy gone |
| `layer3_adapters/controllers/restful/v1/artifact_controller.py` | Merged into /data descriptor |

Remove their `include_router` calls from `__init__.py`.

### 5.2 Rewrite `run_controller.py` routes

Replace the existing node-data/edit/resume routes with the new surface:

```python
# Node-scoped
@router.get("/{run_id}/nodes/{node_id}/schema")
@router.get("/{run_id}/nodes/{node_id}/data")
@router.get("/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata")
@router.patch("/{run_id}/nodes/{node_id}/data")
@router.post("/{run_id}/nodes/{node_id}/submit")

# Run-scoped
@router.get("/{run_id}/artifacts")
@router.post("/{run_id}/resolve")
@router.post("/{run_id}/rerun")
```

Remove: `input-data`, `output`, `data-info`, `artifacts` (list/attach), `edit-node-input`, `resume-input`, `tasks/{id}/resume`.

Keep: `tasks/{id}/complete`, `tasks/{id}/fail`, `tasks/{id}/rerun` (worker-facing).

### 5.3 New DTOs in `dtos/` — with input validation

Create `node_data_dtos.py` using **Pydantic v2** with field constraints, `Literal` enums, and a **discriminated union** on `ArtifactOp`. Pydantic enforces these before the request reaches the use case — invalid payloads return `422 Unprocessable Entity` from FastAPI automatically.

```python
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# --- Shared constrained types ---

IdStr           = Annotated[str, StringConstraints(min_length=1, max_length=128,
                                                    pattern=r"^[A-Za-z0-9_\-.:]+$")]
ArtifactIdStr   = Annotated[str, StringConstraints(min_length=1, max_length=256,
                                                    pattern=r"^[A-Za-z0-9_\-.]+$|^__root__$")]
ColumnNameStr   = Annotated[str, StringConstraints(min_length=1, max_length=128)]
FilterStr       = Annotated[str, StringConstraints(max_length=4096)]
PathExprStr     = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
EtagStr         = Annotated[str, StringConstraints(pattern=r'^(W/)?"[A-Za-z0-9+/:=_\-]+"$',
                                                    max_length=256)]

OutputTypeLit = Literal["string", "number", "boolean", "object", "array"]
RerunStrategyLit = Literal["none", "node_only", "cascade"]
SchemaCompatLit = Literal["strict", "drift_ok"]
SideLit = Literal["input", "output", "both"]
NodeKindLit = Literal["INPUT", "HUMAN_ACTION", "DATA_EDIT", "FILE_UPLOAD"]
SchemaSourceLit = Literal["design_time", "runtime"]

# --- Schema / data response DTOs ---

class GetNodeSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema: Any = Field(..., description="FE-owned auto-form schema; opaque to BE")
    schema_version: str
    schema_source: SchemaSourceLit
    node_kind: NodeKindLit
    node_state: str

class ArtifactDescriptorDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: ArtifactIdStr
    file_id: IdStr
    columns: list[ColumnNameStr]
    parent_row_key_column: ColumnNameStr | None = None
    children: list["ArtifactDescriptorDto"] = Field(default_factory=list)
    artifact_type: Literal["array_field", "nested_array_field"] | None = None
    content_type: str | None = None

class NodeDataDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    etag: EtagStr
    version: int = Field(ge=0)
    scalars: dict[str, Any] = Field(default_factory=dict)
    root: ArtifactDescriptorDto

class GetNodeDataResponse(BaseModel):
    run_id: IdStr
    node_id: IdStr
    task_id: IdStr
    node_kind: NodeKindLit
    node_state: str
    schema_ref: dict
    data: NodeDataDto | None = None
    output: NodeDataDto | None = None
    produced_artifacts: list[str] = Field(default_factory=list)

# --- PATCH request: discriminated union on `op` ---

class RowPathHop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: ArtifactIdStr
    row_index: int = Field(ge=0)
    filter: FilterStr

class UpdateCellOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["update_cell"]
    row_path: list[RowPathHop] = Field(min_length=1, max_length=16)
    column: ColumnNameStr
    value: Any

class BulkUpdateOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["bulk_update"]
    artifact_id: ArtifactIdStr
    row_path: list[RowPathHop] | None = Field(default=None, max_length=16)
    filter: FilterStr
    assignments: dict[ColumnNameStr, Any] = Field(min_length=1)

class InsertRowsOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["insert_rows"]
    artifact_id: ArtifactIdStr
    row_path: list[RowPathHop] | None = Field(default=None, max_length=16)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)

class DeleteRowOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete_row"]
    row_path: list[RowPathHop] = Field(min_length=1, max_length=16)

class DeleteRowsOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete_rows"]
    artifact_id: ArtifactIdStr
    row_path: list[RowPathHop] | None = Field(default=None, max_length=16)
    filter: FilterStr = Field(min_length=1, description="non-empty; use '$filter=true eq true' to wipe")

class ResetArtifactOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["reset_artifact"]
    artifact_id: ArtifactIdStr

class ReplaceArtifactOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["replace_artifact"]
    artifact_id: ArtifactIdStr
    file_id: IdStr
    row_path: list[RowPathHop] | None = Field(default=None, max_length=16)
    schema_compatibility: SchemaCompatLit = "strict"
    expected_columns: list[ColumnNameStr] | None = None

class AddColumnOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add_column"]
    artifact_id: ArtifactIdStr
    column: ColumnNameStr
    output_type: Literal["string", "number", "boolean"]
    default_value: Any = None
    rules: list[dict[str, Any]] | None = None

class DropColumnOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["drop_column"]
    artifact_id: ArtifactIdStr
    column: ColumnNameStr

class RenameColumnOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["rename_column"]
    artifact_id: ArtifactIdStr
    old_name: ColumnNameStr
    new_name: ColumnNameStr

ArtifactOp = Annotated[
    Union[
        UpdateCellOp, BulkUpdateOp,
        InsertRowsOp, DeleteRowOp, DeleteRowsOp,
        ResetArtifactOp, ReplaceArtifactOp,
        AddColumnOp, DropColumnOp, RenameColumnOp,
    ],
    Field(discriminator="op"),
]

class PatchDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rerun_strategy: RerunStrategyLit
    reset_to_input: bool = False
    scalars: dict[str, Any] | None = None
    artifact_ops: list[ArtifactOp] | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _at_least_one_change(self):
        if not self.reset_to_input and not self.scalars and not self.artifact_ops:
            raise ValueError(
                "At least one of reset_to_input, scalars, or artifact_ops must be provided"
            )
        return self

# --- PATCH response ---

class OpResultDto(BaseModel):
    op_index: int = Field(ge=0)
    status: Literal["ok", "failed"]
    rows_affected: int | None = None
    row_ids: list[str] | None = None
    error: dict | None = None

class RerunInfoDto(BaseModel):
    strategy: RerunStrategyLit
    triggered: bool
    degraded_from: RerunStrategyLit | None = None
    reset_node_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)

class PatchDataResponse(BaseModel):
    etag: EtagStr
    version: int = Field(ge=0)
    file_lineage: dict[str, str] = Field(default_factory=dict)
    results: list[OpResultDto]
    rerun: RerunInfoDto

# --- Other ---

class SubmitNodeResponse(BaseModel):
    task_id: IdStr
    node_state: str
    downstream_dispatched: bool

class ArtifactRefDto(BaseModel):
    file_id: IdStr
    filename: str | None = None
    content_type: str
    size_bytes: int = Field(ge=0)
    produced_by_node: str
    produced_by_task: str
    registered_at: str

class GetRunArtifactsResponse(BaseModel):
    artifacts: dict[str, ArtifactRefDto]

class ResolvePathsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[PathExprStr] = Field(min_length=1, max_length=100)
    task_id: IdStr | None = None

class ResolveResultDto(BaseModel):
    path: str
    status: Literal["ok", "error"]
    kind: Literal["scalar", "file_ref", "artifact", "artifact_row", "artifact_column"] | None = None
    value: Any | None = None
    error: dict | None = None

class ResolvePathsResponse(BaseModel):
    results: list[ResolveResultDto]

class RerunRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_node_id: IdStr | None = None
    rerun_strategy: RerunStrategyLit = "cascade"
    variables: dict[str, Any] | None = None

class RerunRunResponse(BaseModel):
    reset_node_ids: list[str] = Field(default_factory=list)
    dispatched_task_ids: list[str] = Field(default_factory=list)
```

**What Pydantic catches before the use case runs** (FastAPI auto-returns 422):

- Wrong or missing `op` → discriminator rejects unknown variants.
- `filter: ""` on `delete_rows` — min_length=1.
- `row_path: []` on `update_cell` / `delete_row` — min_length=1.
- `rows: []` on `insert_rows` — min_length=1.
- `rerun_strategy: "unknown"` — Literal rejects.
- `row_index: -1` — ge=0 constraint.
- Empty `PatchDataRequest` — `model_validator` rejects.
- Unknown fields (typos) — `extra="forbid"`.
- Oversized batches — `max_length=1000` on `artifact_ops`.
- Excessive nesting — `max_length=16` on `row_path`.

**Business-logic validation** (still handled by `NodeInputValidator` inside the use case): rule dispatch, column-name uniqueness, reserved columns, schema compatibility, parent_row_id consistency, file existence. These produce `422` with specific `code` (`STATIC_SCHEMA_IMMUTABLE`, `ROW_PATH_AMBIGUOUS`, etc.) via the error envelope — distinct from Pydantic's validation error shape.

### 5.4 ETag / If-Match header handling

Use FastAPI `Header()` dependencies for declarative header handling (also surfaces them in OpenAPI):

```python
from fastapi import Header, HTTPException, Response

IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]
IfMatch     = Annotated[str | None, Header(alias="If-Match")]

@router.get("/{run_id}/nodes/{node_id}/schema",
            response_model=ApiEnvelope[GetNodeSchemaResponse],
            responses={304: {"description": "Not Modified"},
                       404: {"model": ApiEnvelope[ApiErrorPayload]}})
async def get_node_schema(
    run_id: str, node_id: str,
    response: Response,
    if_none_match: IfNoneMatch = None,
    task_id: str | None = None,
    request: Request = None,
):
    use_case = request.app.state.container["get_node_schema"]
    result = await use_case.execute(GetNodeSchemaCommand(run_id=run_id, node_id=node_id, task_id=task_id))
    if if_none_match and if_none_match == result.schema_etag:
        return Response(status_code=304)
    response.headers["ETag"] = result.schema_etag
    response.headers["Cache-Control"] = "private, max-age=30"
    return ok_envelope(result.to_dto())

@router.patch("/{run_id}/nodes/{node_id}/data", ...)
async def patch_node_data(
    run_id: str, node_id: str,
    body: PatchDataRequest,         # Pydantic-validated before this runs
    response: Response,
    if_match: IfMatch = None,
    ...,
):
    if not if_match:
        raise AppError(code="PRECONDITION_REQUIRED", status_code=409,
                       message="If-Match header is required on PATCH /data")
    result = await use_case.execute(EditNodeDataCommand(..., if_match=if_match))
    response.headers["ETag"] = result.new_etag
    return ok_envelope(result.to_dto())
```

Rules:

- `If-None-Match` on `GET /schema`, `GET /data`, `GET /artifacts` — 304 on match, `ETag` header on 200.
- `If-Match` on `PATCH /data` — **controller raises `AppError`** (`PRECONDITION_REQUIRED`, 409) if absent; use case raises `AppError` (`PRECONDITION_FAILED`, 412) on stale.
- Weak-ETag format `W/"sha256:..."` is preserved through comparison.

### 5.5 Error signalling — use `AppError`, not `HTTPException`

The codebase already has `AppError` + an `app_error_handler` registered in `create_fastapi_app()` that emits the `{status: "error", error: {code, message, details}}` envelope. **Controllers and use cases raise `AppError`; no `HTTPException` is used.**

Extend `layer2_application/errors.py` with all codes (and their default HTTP statuses) required by this design:

```python
# layer2_application/errors.py — add:

APP_ERROR_STATUSES: dict[str, int] = {
    # Existing entries preserved; add/ensure:
    "RUN_NOT_FOUND":             404,
    "RUN_NOT_READY":             409,
    "NODE_NOT_FOUND":            404,
    "ARTIFACT_NOT_FOUND":        404,
    "FORBIDDEN":                 403,
    "PRECONDITION_REQUIRED":     409,
    "PRECONDITION_FAILED":       412,
    "INPUT_EVICTED":             410,
    "VALIDATION_FAILED":         422,
    "STATIC_SCHEMA_IMMUTABLE":   422,
    "ROW_PATH_AMBIGUOUS":        422,
    "NO_INPUT_REFERENCE":        422,
    "FILE_NOT_FOUND":            422,
    "MISSING_SYNTHETIC_COLUMNS": 422,
    "COLUMN_MISMATCH":           422,
    "PARENT_ROW_ID_MISMATCH":    422,
    "ROW_NOT_FOUND":             422,
    "BATCH_TOO_LARGE":           413,
    "NODE_STATE_INVALID":        423,
    "INVALID_PATH_SYNTAX":       400,
}

class AppError(Exception):
    def __init__(self, *, code: str, message: str,
                 status_code: int | None = None,
                 details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code or APP_ERROR_STATUSES.get(code, 500)
        self.details = details or {}
        super().__init__(message)
```

Verify `app_error_handler` in `layer3_adapters/controllers/restful/v1/middleware/error_handler.py` serializes `details` (if it doesn't already). Update minimally if needed — don't rewrite the handler.

The `ResponseWrapperMiddleware` at `layer3_adapters/controllers/restful/v1/middleware/response_wrapper.py:68-84` already passes through pre-wrapped envelopes unchanged, so `AppError`-produced responses aren't double-wrapped.

### 5.6 Route removal / addition order

To avoid mount-time path collisions:

1. In `run_controller.py`, **delete** old route handlers (`input-data`, `output`, `data-info`, `artifacts`, `edit-node-input`, `resume-input`, `tasks/{id}/resume`) first, in one commit.
2. In the same commit, add the new routes (`schema`, `data`, `artifacts/{aid}/odata`, `data` PATCH, `submit`, run-level `artifacts` / `resolve` / `rerun`). Their paths don't clash with anything remaining.
3. **Delete** `file_controller.py` and `artifact_controller.py` and remove their `include_router()` calls from `__init__.py` (lines 54–66).
4. Ensure existing `tasks/{id}/complete|fail|rerun` worker-facing routes stay intact.

### 5.7 Bootstrap wiring (`bootstrap.py`)

Add new entries to `available_dependencies` (lines ~384–408):

```python
available_dependencies = {
    ...  # existing entries
    "node_input_etag_calculator": node_input_etag_calculator,
    "node_input_validator":       node_input_validator,
    "row_path_resolver":          row_path_resolver,
    "run_lock_registry":          run_lock_registry,
}
```

Feature auto-discovery (`load_features()`) picks up the new features automatically. No route registry changes needed — `run_controller.router` is already included.

Also: `layer2_application/errors.py` must list all new `code` constants (`STATIC_SCHEMA_IMMUTABLE`, `ROW_PATH_AMBIGUOUS`, `NO_INPUT_REFERENCE`, `FILE_NOT_FOUND`, `MISSING_SYNTHETIC_COLUMNS`, `COLUMN_MISMATCH`, `PARENT_ROW_ID_MISMATCH`, `ROW_NOT_FOUND`, `BATCH_TOO_LARGE`, `INVALID_PATH_SYNTAX`) so use cases reference them by symbol, not string.

### 5.8 Controller `__init__.py`

Update `create_fastapi_app()` to:
- Remove `file_controller`, `artifact_controller` includes
- Keep `run_controller` (now has all node + run routes)

---

## Phase 5b: OpenAPI / Swagger Quality

FastAPI auto-generates `/docs` (Swagger UI) and `/openapi.json` from Pydantic models. Quality requires deliberate use of decorator arguments. These apply to every new route.

### 5b.1 Route decorator conventions

```python
@router.patch(
    "/{run_id}/nodes/{node_id}/data",
    response_model=ApiEnvelope[PatchDataResponse],
    status_code=200,
    summary="Edit node data (atomic batch)",
    description="Apply scalars + artifact ops to the working copy. Requires If-Match.",
    tags=["node-data"],
    responses={
        200: {"description": "Edit committed", "headers": {"ETag": {"schema": {"type": "string"}}}},
        304: {"description": "Not modified (GETs only)"},
        400: {"model": ApiEnvelope[ApiErrorPayload], "description": "Invalid syntax"},
        403: {"model": ApiEnvelope[ApiErrorPayload]},
        404: {"model": ApiEnvelope[ApiErrorPayload]},
        409: {"model": ApiEnvelope[ApiErrorPayload], "description": "If-Match missing"},
        410: {"model": ApiEnvelope[ApiErrorPayload]},
        412: {"model": ApiEnvelope[ApiErrorPayload], "description": "Stale ETag"},
        413: {"model": ApiEnvelope[ApiErrorPayload]},
        422: {"model": ApiEnvelope[ApiErrorPayload], "description": "Validation failed"},
        423: {"model": ApiEnvelope[ApiErrorPayload]},
    },
)
```

### 5b.2 Tags

Group by audience:

| Tag | Routes |
|---|---|
| `node-schema` | `GET /schema` |
| `node-data` | `GET /data`, `PATCH /data`, `GET /artifacts/{aid}/odata`, `POST /submit` |
| `run-artifacts` | `GET /runs/{id}/artifacts` |
| `run-resolve` | `POST /runs/{id}/resolve` |
| `run-rerun` | `POST /runs/{id}/rerun` |
| `variable-suggestions` | `GET /variable-suggestions` |

### 5b.3 Examples on DTOs

Embed `json_schema_extra` so Swagger UI renders realistic bodies:

```python
class PatchDataRequest(BaseModel):
    ...
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "rerun_strategy": "cascade",
                    "artifact_ops": [
                        {"op": "update_cell",
                         "row_path": [{"artifact_id":"orders","row_index":3,"filter":"$filter=region eq 'APAC'"}],
                         "column": "qty", "value": 150}
                    ]
                },
                {
                    "rerun_strategy": "none",
                    "reset_to_input": True
                },
                {
                    "rerun_strategy": "cascade",
                    "artifact_ops": [
                        {"op": "replace_artifact",
                         "artifact_id": "orders",
                         "file_id": "f_enriched_v2",
                         "schema_compatibility": "drift_ok"}
                    ]
                }
            ]
        }
    )
```

Include examples on `ResolvePathsRequest`, `RerunRunRequest`, `GetNodeDataResponse` too.

### 5b.4 Error payload DTO

To make error responses show up in `/docs`:

```python
class ApiErrorPayload(BaseModel):
    code: str = Field(examples=["PRECONDITION_FAILED", "VALIDATION_FAILED"])
    message: str
    details: dict[str, Any] | None = None
```

### 5b.5 `ApiEnvelope[T]` generic

Use Pydantic's generic model so the auto-generated schema distinguishes success (`data`) vs error (`error`):

```python
from typing import Generic, TypeVar
T = TypeVar("T")

class ApiEnvelope(BaseModel, Generic[T]):
    status: Literal["ok", "error"]
    data: T | None = None
    error: ApiErrorPayload | None = None
```

FastAPI supports generic `response_model` since Pydantic v2 — Swagger renders per-endpoint envelope schemas.

### 5b.6 Header docs in OpenAPI

`Header()` dependencies automatically appear in `/docs` under "Parameters → header". Response headers (`ETag`, `Cache-Control`) are declared via the `responses={}` dict as shown in §5b.1.

### 5b.7 Discriminated-union rendering

Pydantic v2 discriminated unions render in Swagger UI as an "oneOf" with the `op` selector. No extra work — the `Annotated[Union[...], Field(discriminator="op")]` pattern from §5.3 is sufficient.

### 5b.8 Verification

- `GET /docs` renders without errors; all 11 node-input / run-level routes present with correct tags.
- `GET /openapi.json` validates against the OpenAPI 3.1 schema (e.g., via `openapi-spec-validator`).
- Each route shows: parameters (path, query, header), request body schema with examples, every documented response code with shape.
- PATCH `artifact_ops` renders as a selector of 10 op variants with their own schemas.

---

## Phase 6: Tests

### 6.1 Tests to delete

| Test file | Reason |
|---|---|
| `tests/unit/test_get_node_input_data.py` | Feature replaced |
| `tests/unit/test_edit_node_input.py` | Feature replaced |
| `tests/unit/test_resume_input_node.py` | Feature replaced |

### 6.2 New unit tests

| Test file | Covers |
|---|---|
| `tests/unit/test_data_reference.py` | `build_file_ref` recursive shape; `walk_file_ref`; `find_artifact`; `register_artifacts` metadata-object shape + provenance |
| `tests/unit/test_node_input_etag_calculator.py` | Determinism, sensitivity, ordering invariance |
| `tests/unit/test_row_path_resolver.py` | 1/2/3-deep hops; with/without filter; out-of-bound index; nested `__parent_row_id` chaining |
| `tests/unit/test_node_input_validator.py` | Per-rule-method dispatch; nested propagation; unknown-method rejection; reserved-column rejection; column-op validators (duplicate_column, reserved_column, output_type) |
| `tests/unit/test_data_store_service.py` | `materialize_tree` round-trip on 3-level nested fixture |
| `tests/unit/test_get_node_schema.py` | Happy path; ETag computation |
| `tests/unit/test_get_node_data.py` | Descriptor tree building; produced_artifacts filtering |
| `tests/unit/test_edit_node_data.py` | Each op type (update_cell, bulk_update, insert_rows, delete_row, delete_rows, reset_artifact, replace_artifact, reset_to_input, add_column, drop_column, rename_column); atomic rollback on failure; rerun dispatch per strategy; ETag + schema-ETag bumps; static-schema column-op rejection; `delete_row` with missing terminal hop → 422 ROW_PATH_AMBIGUOUS; `delete_rows` with empty filter → 422 ROW_PATH_AMBIGUOUS; `replace_artifact` with non-existent file_id → 422 FILE_NOT_FOUND; missing `__row_id` → 422 MISSING_SYNTHETIC_COLUMNS; strict column mismatch → 422 COLUMN_MISMATCH; nested replace with wrong `__parent_row_id` → 422 PARENT_ROW_ID_MISMATCH; drift_ok on static schema → 422 STATIC_SCHEMA_IMMUTABLE |
| `tests/unit/test_submit_node.py` | Validation pass → transition; validation fail → 422; non-pending → 423 |
| `tests/unit/test_get_run_artifacts.py` | Registry shape; empty registry |
| `tests/unit/test_resolve_variable_paths.py` | Scalar, file_ref, artifact_row, error paths; batch behaviour |
| `tests/unit/test_get_variable_suggestions.py` | **Modify existing** — add tests for recursive schema walk; file_ref auto-expansion; `$artifacts` metadata-object suggestions; `kind` field |
| `tests/unit/test_dag_traversal.py` | `collect_downstream_nodes_to_reset` — linear, branching, merge-point cases |

### 6.3 Integration tests

| Test file | Scenario |
|---|---|
| `tests/integration/test_node_data_flow.py` | Full pending-node lifecycle: GET schema → GET data → scoped OData → PATCH (mixed ops) → verify ETag → POST submit → downstream dispatched |
| `tests/integration/test_edit_rerun_cascade.py` | Edit completed node with `rerun_strategy=cascade` → downstream nodes reset → dispatched |
| `tests/integration/test_etag_concurrency.py` | Stale `If-Match` → 412; missing → 409 |
| `tests/integration/test_run_artifacts.py` | Complete FILE_UPLOAD node → GET /runs/{id}/artifacts → metadata object shape |
| `tests/integration/test_data_edit_column_ops.py` | DATA_EDIT node: add_column → update_cell on new column → delete_row → drop_column → verify schema ETag bumps each time and GET /schema reflects the updated runtime schema; also verify column ops on INPUT/HUMAN_ACTION → 422 STATIC_SCHEMA_IMMUTABLE |
| `tests/integration/test_replace_artifact.py` | External-service flow: upload file to file service (mock) → get file_id → PATCH /data with replace_artifact → verify artifact swapped, data ETag bumped, downstream cascade triggered. Nested variant with `row_path` and `__parent_row_id` scoping. Drift_ok: verify runtime schema updated and schema ETag bumped. |
| `tests/integration/test_resolve_paths.py` | POST /resolve with scalar + nested-artifact + $artifacts paths → correct values |
| `tests/integration/test_openapi.py` | Hit `/openapi.json`; validate it parses as OpenAPI 3.1; assert each new route + tag + response status is present; run `openapi-spec-validator` on it. |
| `tests/integration/test_patch_dto_validation.py` | Each Pydantic rejection case → 422 from FastAPI before use case runs: empty `artifact_ops` + no scalars + no reset → rejected; unknown `op` value; `delete_rows.filter` empty; `row_index: -1`; unknown field; `paths` list > 100 on resolve. |

### 6.4 Test fixtures / conftest additions

New fixtures in `tests/conftest.py` for reuse across unit tests:

```python
@pytest.fixture
def mock_file_service_client(mocker):
    """Mock IFileServiceClient with head/query_odata/download/upload/delete."""
    client = mocker.AsyncMock()
    client._files = {}  # in-memory store: file_id -> {content, columns, rows}
    async def _upload(content, *, tenant_id, content_type="text/csv"):
        fid = f"f_{uuid4().hex[:8]}"
        client._files[fid] = {"content": content, "content_type": content_type, ...}
        return fid
    client.upload.side_effect = _upload
    client.head.side_effect = lambda fid, *, tenant_id: client._files.get(fid)
    return client

@pytest.fixture
def file_ref_tree_factory():
    """Builds a recursive __file_ref dict from a nested Python structure.
    Example:
        file_ref_tree_factory({
            "customer_name": "Acme",
            "orders": [
                {"sku": "A1", "line_items": [{"part_no": "X"}]},
            ]
        })
    """
    ...

@pytest.fixture
def run_lock_registry():
    return RunLockRegistry()

@pytest.fixture
def node_input_validator():
    return NodeInputValidator()
```

Mock `RowPathResolver` separately per-test via `mocker.AsyncMock()` spec'd against the real class — row-path resolution is an I/O step most tests want to stub.

---

## Execution Order

```
Phase 1  ─── data_reference.py changes + call-site updates + audit
              (enables everything else; low risk, small diff)
   │
Phase 2  ─── domain services (validator, etag calculator, row_path_resolver)
              (no external deps beyond file_service_client; can unit-test immediately)
   │
Phase 3  ─── use cases (new features + delete old)
   │         3a: get_node_schema, get_node_data, get_run_artifacts (read-only, simplest)
   │         3b: submit_node, query_node_artifact
   │         3c: edit_node_data (most complex — depends on Phase 2 services)
   │         3d: rerun_run
   │         3e: resolve_variable_paths
   │         3f: modify get_variable_suggestions
   │
Phase 4  ─── recursive storage (data_store_service, file_backed_resolver, dag_traversal)
              (can partially overlap with Phase 3; edit_node_data depends on this)
   │
Phase 5  ─── controllers + DTOs + header/error mapping + bootstrap wiring
              (depends on all use cases existing)
   │
Phase 5b ─── OpenAPI/Swagger quality (decorators, tags, examples, error payload DTO)
              (parallel with Phase 5; one commit)
   │
Phase 6  ─── tests (can start unit tests during Phase 2; integration tests after Phase 5)
```

Phases 1–2 are strictly sequential. Phases 3–4 can overlap. Phase 5 + 5b follow them. Phase 6 is continuous.

---

## Verification

### Smoke test (manual, `INFRA_MODE=mock`)

```bash
cd workflow-control-plane-service && python main.py
```

1. Create a workflow with a HUMAN_ACTION node that has nested tables.
2. Start a run → node goes PENDING.
3. `GET /runs/{id}/nodes/{nid}/schema` → 200 + ETag. Re-request with `If-None-Match` → 304.
4. `GET /runs/{id}/nodes/{nid}/data?side=input` → descriptor tree, no inline rows.
5. `GET /runs/{id}/nodes/{nid}/artifacts/{aid}/odata?$top=50&$count=true` → rows from file service.
6. `PATCH /runs/{id}/nodes/{nid}/data` with `If-Match`, mixed ops, `rerun_strategy=none` → 200 + new ETag.
7. Repeat PATCH with old ETag → 412.
8. `POST /runs/{id}/nodes/{nid}/submit` → 200, downstream dispatches.
9. `GET /runs/{id}/artifacts` → registry with metadata objects.
10. `POST /runs/{id}/resolve` with `["$.node.scalar", "$artifacts.name.file_id"]` → resolved values.

### Test suite

```bash
python3 -m pytest tests/ -v
```

All new + modified tests pass. No regressions in existing tests (audit changes to `register_artifacts` callers).
