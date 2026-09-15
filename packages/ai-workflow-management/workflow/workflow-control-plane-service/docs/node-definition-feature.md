# Node Definition — Feature Reference (Backend)

Service: **workflow-control-plane-service**
Base URL (local): `http://localhost:8001` · API prefix: `/api/v1` · Router prefix: `/node-definitions`

This document is the **reference** for the Node Definition feature: what it is, the API
surface, how each endpoint behaves end-to-end, and — importantly — **what is supported and
what is not**.

For step-by-step *how-to* material (worked examples, AutoFormField schema syntax, expression
syntax) see the companion guides, which this doc does not duplicate:

- `docs/node-definition-integration-guide.md` — building business nodes + workflow integration
- `docs/node-definition-mapping-guide.md` — input/output mapping & worker field reference

---

## 1. What is a Node Definition?

A **Node Definition** is a reusable, tenant-scoped **business-node template**. It wraps a
base capability — a *worker* (e.g. `http-request-worker`) or a built-in *control-flow* node —
into a friendlier node that appears in the workflow palette under the **`business`** category.

It exists so a builder can:

- **Hide complexity** — lock the method/URL/auth/timeout into `locked_defaults` so end users
  never see them.
- **Expose a custom form** — declare only the fields users fill in, via `input_schema`.
- **Declare outputs** — describe what the node produces, via `output_schema`.
- **Transform data** — `input_mapping` translates user fields → worker fields at dispatch;
  `output_mapping` extracts/renames raw worker output at completion.
- **Inherit** — a child definition can refine a parent (`parent_definition_id`).

### Node Definition vs. a node in a workflow

| | Node Definition | Workflow node |
|---|---|---|
| Lives in | `AIW_NODE_DEFINITIONS` table (this service) | inside a `Workflow`'s `nodes[]` |
| Is | a **template** (the class) | an **instance** (the object) |
| References | a base worker / base type | a node definition via `node_definition_id` |
| At workflow save | resolved + **snapshotted** into `node.config._node_definition` (FE-facing) and `node.config._node_internals` (runtime-facing) | carries the snapshot + per-instance `input_binding` |

> The snapshot is taken at **workflow save time**. Editing a definition does **not**
> retroactively change workflows already saved against it — re-save the workflow to re-enrich.

### Entity

`app/layer1_domain/entities/node_definition.py:14`

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | `str` | uuid4 | |
| `tenant_id` | `str` | `"system"` | required (non-blank) |
| `name` | `str` | `""` | required (non-blank) |
| `description` | `str` | `""` | |
| `spec_version` | `str` | `"1.0.0"` | contract identity (semver). **Phase 0 — default only, no resolution yet** |
| `parent_definition_id` | `str \| None` | `None` | inheritance link |
| `base_type` | `NodeType` | `TASK` | see enum below |
| `base_worker_id` | `str \| None` | `None` | bare `worker_type`; a catalog id `worker:<type>` is auto-stripped on every construction (`__post_init__`, `:39`) |
| `locked_defaults` | `dict` | `{}` | config injected at dispatch; users cannot override |
| `input_schema` | `list[dict]` | `[]` | AutoFormField form definition |
| `output_schema` | `list[dict]` | `[]` | AutoFormField (readonly) output description |
| `input_mapping` | `dict` | `{}` | user fields → worker fields (dispatch time) |
| `output_mapping` | `dict` | `{}` | raw worker output → run-context vars (completion time) |
| `icon` | `str` | `""` | palette icon name |
| `color` | `str` | `""` | palette color (hex) |
| `tags` | `list[str]` | `[]` | |
| `ports` | `dict` | `{}` | `{in:[…], out:[…]}`; auto-populated from the node spec if empty |
| `data_mode` | `str` | `"json"` | one of `json`, `csv_single`, `csv_multi` |
| `edit_mode` | `str` | `"none"` | one of `none`, `before_dispatch`, `after_dispatch` |
| `created_at` / `updated_at` | ISO str | now | |

**Validation** (`validate()`, `:54`): `name` required, `tenant_id` required,
`parent_definition_id` cannot equal `id`, `base_worker_id` required when `base_type == TASK`
**and** there is no parent (children inherit the worker), `data_mode` ∈ `VALID_DATA_MODES`,
`edit_mode` ∈ `VALID_EDIT_MODES`.

### `NodeType` enum

`app/layer1_domain/value_objects/node_type.py:6`

```
TASK, PARALLEL, CONDITION, LOOP, SWITCH, MERGE, TRIGGER, WORKFLOW, COMPUTE,
INPUT, HUMAN_ACTION, DATA_EDIT, TEMPLATE_DATA_EDIT, FILE_UPLOAD,
LOOP_EXIT, LOOP_CONTINUE, OUTPUT, APPROVAL_FLOW, SET_VARIABLE, PROMPT
```

Each type's *runtime class* (worker / engine / pending) is declared by its **node spec**
(`app/layer1_domain/node_spec/`) and is the single source of truth used by the test feature
(§4) and the orchestrator.

---

## 2. API endpoints

All responses are wrapped by a `ResponseWrapperMiddleware` into an envelope:

```jsonc
// success
{ "status": "ok", "data": { /* controller payload */ } }
// error
{ "status": "error", "error": { "code": "…", "message": "…", "details": { } } }
```

Controller: `app/layer3_adapters/controllers/restful/v1/node_definition_controller.py`
Mounted at `:88` of `…/restful/v1/__init__.py` with prefix `/node-definitions`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/node-definitions` | Create |
| `GET` | `/node-definitions?tenant_id=…` | List (OData paging) |
| `GET` | `/node-definitions/{id}` | Get one |
| `PATCH` | `/node-definitions/{id}` | Partial update |
| `DELETE` | `/node-definitions/{id}` | Delete (blocked if children exist) |
| `POST` | `/node-definitions/{id}/test` | Test the definition standalone |

> Palette discovery for the editor is a separate endpoint: `GET /node-palette?tenant_id=…`
> (returns business nodes under the `business` category). Covered in the integration guide.

### POST `/node-definitions` — Create

Request body (`CreateNodeDefinitionRequest`, `controller:22`):

```jsonc
{
  "tenant_id": "system",
  "name": "Google Search",          // required
  "description": "",
  "parent_definition_id": null,
  "base_type": "TASK",              // default "TASK"
  "base_worker_id": "http-request-worker",
  "locked_defaults": {},
  "input_schema": [],
  "output_schema": [],
  "input_mapping": {},
  "output_mapping": {},
  "icon": "", "color": "", "tags": [],
  "ports": {}, "data_mode": "json", "edit_mode": "none"
}
```

Response `data`: the full created definition (all entity fields + `created_at`/`updated_at`).
On failure returns `VALIDATION_ERROR` (400) with `details.errors` (the validate() messages),
or `PARENT_DEFINITION_NOT_FOUND` (404) / `TENANT_MISMATCH` (400) for bad parents.

### GET `/node-definitions` — List

Query params: `tenant_id` (direct param wins; falls back to `$filter`, then `"system"`),
plus OData `$skip`, `$top` (default 20, **capped at 100**), `$orderby` (e.g. `name asc`,
`created_at desc`). `offset`/`limit` accepted as aliases. (`odata_params.py`)

Response `data`: `{ "items": [...], "total": <int>, "$skip": n, "$top": m }`.
Filtering is **tenant-only** in the repo; sorting & paging happen in the use case
(`list_node_definitions_use_case.py:30`).

### GET `/node-definitions/{id}` — Get one

Response `data`: the full definition, or `NODE_DEFINITION_NOT_FOUND` (404).

### PATCH `/node-definitions/{id}` — Update

Request body (`PatchNodeDefinitionRequest`, `controller:46`): every field optional.
**PATCH semantics**: only provided keys change (`model_dump(exclude_none=True)`).
`base_worker_id` is normalized (`worker:` prefix stripped). Re-validates inheritance chain
when `parent_definition_id` changes. Bumps `updated_at`. Returns updated definition, or
`NODE_DEFINITION_NOT_FOUND` / `VALIDATION_ERROR` / `PARENT_DEFINITION_NOT_FOUND` /
`TENANT_MISMATCH`.

> Caveat: `parent_definition_id` uses a `_UNSET` sentinel internally so `null` means
> "clear the parent". But the controller uses `exclude_none=True`, so a `null` in the JSON
> body is dropped before it reaches the command — i.e. **clearing a parent via the HTTP API
> is currently not reachable** (`update_node_definition_use_case.py:25`, `controller:153`).

### DELETE `/node-definitions/{id}` — Delete

Blocks deletion if any other definition (same tenant) references this one as
`parent_definition_id` → `CANNOT_DELETE_IN_USE` (409) with `details.children` (names).
Otherwise `{ "deleted": true }`. Missing → `NODE_DEFINITION_NOT_FOUND` (404).

> Note: it does **not** check whether saved workflows reference the definition — only
> child *definitions*. Workflows hold a snapshot, so they keep working after deletion.

### POST `/node-definitions/{id}/test` — Test

See §4. Request: `{ "input_data": { … } }`. The status-code contract is nuanced.

---

## 3. How it works end-to-end

```
HTTP request
  → controller (DTO validation, OData parse, envelope/error mapping)
    → UseCase.execute(Command)               app/layer2_application/features/<feature>/…
        → NodeDefinition entity (.validate(), invariants)
        → NodeDefinitionResolver             (parent-chain cycle/depth checks)
        → INodeDefinitionRepository          (save / get_by_id / list_by_tenant / delete)
            → HanaNodeDefinitionRepository → AIW_NODE_DEFINITIONS  (or in-memory repo for tests)
```

Each use case returns a **dataclass result**; controllers `dataclasses.asdict(...)` it and map
`error_code` → HTTP status via `ErrorCode` (`app/layer2_application/errors.py`).

### Persistence

`app/layer4_frameworks/persistence/hana/hana_node_definition_repo.py`

- Table `AIW_NODE_DEFINITIONS`. JSON-typed fields (`locked_defaults`, `*_schema`,
  `*_mapping`, `tags`, `ports`) stored as NCLOB JSON; `base_type` stored as its string value.
- `save()` is an `UPSERT … WITH PRIMARY KEY` (create and update share the same path).
- `list_by_tenant()` = `WHERE TENANT_ID = ?` (no DB-side paging — done in the use case).
- A pure in-memory repo exists for tests: `…/persistence/memory/memory_node_definition_repo.py`.

### Inheritance resolution

`app/layer2_application/definition/node_definition_resolver.py`

- `_build_chain()` walks `parent_definition_id` leaf→root; reversed to `[root … leaf]`.
- `validate_parent_chain()` runs **before** save (create & update) to reject cycles
  (`CircularInheritanceError`) and depth > `MAX_INHERITANCE_DEPTH = 10`
  (`InheritanceDepthExceededError`).
- **Merge strategy** (`_merge_chain`, child overrides parent):

  | Field | Strategy |
  |---|---|
  | `locked_defaults` | shallow merge (child keys win) |
  | `input_schema` / `output_schema` | child **replaces** if non-empty |
  | `input_mapping` / `output_mapping` | shallow merge (child keys win) |
  | `base_worker_id` | child wins if set |
  | `data_mode` / `edit_mode` | child wins if non-default |
  | `icon` / `color` | child wins if non-empty |
  | `ports` | child replaces if non-empty |
  | `tags` | **union** (deduplicated) |

### Runtime (where the snapshot is used)

At **workflow save**, the resolved definition is embedded into the node config; at
**dispatch** the task handler merges `user_input < input_mapping result < locked_defaults`
(locked always wins); at **completion** `output_mapping` runs against the raw worker output
(`$_output`) and the result is stored in the run context as the node's output. Full lifecycle
diagram in the integration guide.

---

## 4. The Test feature (supported vs. not)

`app/layer2_application/features/test_node_definition/use_cases/test_node_definition_use_case.py`

Lets you exercise a definition **in isolation** (no workflow, no canvas) against a sample
`input_data`. Routing is by `base_type`:

| `base_type` group | Members | Behavior |
|---|---|---|
| **TASK / worker** | `TASK` | Applies `input_mapping` + merges `locked_defaults`, calls the worker synchronously (`_test_worker`) |
| **ENGINE — inline-testable** | `CONDITION`, `SWITCH`, `COMPUTE`, `SET_VARIABLE`, `MERGE` | Runs the node's handler inline via `StandaloneNodeRunner` against a synthetic single-node context |
| **PENDING** | `INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, `FILE_UPLOAD`, … | **Not testable here** → `NODE_NOT_TESTABLE_STANDALONE` (422) |
| **ENGINE — deferring** | `LOOP`, `WORKFLOW`, `PARALLEL` | **Not testable here** (need run/topology) → 422 |

The set of inline-testable types is `StandaloneNodeRunner.INLINE_TESTABLE`
(`standalone_node_runner.py:50`); PENDING vs. deferring is derived from the node specs'
`runtime` (`node_types_for_runtime`).

**Standalone limitations** (no ambient engine scope / no upstream graph,
`standalone_node_runner.py:15`):
- `{{$variables.X}}` (SET_VARIABLE runtime namespace) does **not** resolve.
- `{{$<upstreamNode>.field}}` references do **not** resolve.
- Only `{{$input.x}}` / `{{$custom.x}}` (the supplied `input_data`) resolve.
- The runner refuses to start deferred work (child run / loop body / unmet merge quorum) —
  raises `NotTestableStandalone` as a second line of defence.

**Result** (`TestNodeDefinitionResult`): `node_type`, `worker_type`, `resolved_input`,
`status` (`COMPLETED` / `FAILED`), `outputs`, `handler_output`, `branch`, `output_reference`,
`execution_time_ms`, and on request-level failure `error` + `error_code` + `error_details`.

**Status-code contract** (`controller:175`):
- A request-level problem (not found, not testable, service not configured, validation)
  carries an `error_code` → mapped to its HTTP status (404 / 422 / 503 / 400).
- A **worker/handler outcome** — `COMPLETED`, or `FAILED` *without* an `error_code` — is a
  **successful test** → **HTTP 200** with the outcome in the body. (A failing worker is a
  valid test result, not an API error.)

Configuration requirements: the worker path needs a `worker_registry_client` with
`execute_task` (else `SERVICE_NOT_CONFIGURED`, 503); the engine path needs a
`handler_registry` (else those types fall through to 422).

---

## 5. Capability matrix — supported / not supported

**Supported**
- Full CRUD + standalone test over tenant-scoped business-node templates.
- `base_type = TASK` over any registered worker (primary use case).
- Inline test of `CONDITION`, `SWITCH`, `COMPUTE`, `SET_VARIABLE`, `MERGE`.
- Single-parent inheritance with cycle + depth(10) guards and a defined merge strategy.
- AutoFormField `input_schema` / `output_schema`; `input_mapping` / `output_mapping`;
  `locked_defaults`; ports auto-populated from the node spec.
- OData list paging/sorting; `worker:<type>` id normalization; envelope error codes.
- `data_mode` (`json`/`csv_single`/`csv_multi`) and `edit_mode`
  (`none`/`before_dispatch`/`after_dispatch`).

**Not supported / partial / gotchas**
- **Versioning** — `spec_version` exists but is **Phase 0**: default `1.0.0` only, no
  resolution/pinning at dispatch yet (`node_definition_resolver.py` `resolve` /
  `resolve_worker_spec_version` are formalized but not yet wired into the engine).
- **Testing PENDING / LOOP / WORKFLOW / PARALLEL** standalone → 422 (use the canvas test).
- **Clearing a parent over HTTP** — `null` is stripped by `exclude_none=True` (see PATCH).
- **Cross-tenant** parents rejected (`TENANT_MISMATCH`); list/get are tenant-scoped.
- **Delete** checks child *definitions* only — not workflows referencing the snapshot.
- **Editing a definition does not re-enrich** existing saved workflows automatically.
- **No DB-side filtering/pagination** beyond tenant — `list_by_tenant` loads all rows for
  the tenant, then the use case sorts/pages in memory.
- `input_mapping` in the test path resolves against `{"custom": input_data}` only; expressions
  referencing upstream nodes or runtime variable namespaces won't resolve standalone.

---

## 6. Key source map

| Concern | Path |
|---|---|
| Entity + validation | `app/layer1_domain/entities/node_definition.py` |
| Node types | `app/layer1_domain/value_objects/node_type.py` |
| Controller / DTOs / routing | `app/layer3_adapters/controllers/restful/v1/node_definition_controller.py` |
| Create / Get / List / Update / Delete / Test use cases | `app/layer2_application/features/<*>_node_definition/use_cases/` |
| Inheritance resolver | `app/layer2_application/definition/node_definition_resolver.py` |
| Standalone test runner | `app/layer2_application/orchestration/standalone_node_runner.py` |
| Error codes | `app/layer2_application/errors.py` |
| Response envelope / OData | `app/layer3_adapters/controllers/restful/v1/dtos/{response_envelope,api_envelope,odata_params}.py` |
| HANA repo / in-memory repo | `app/layer4_frameworks/persistence/{hana,memory}/*_node_definition_repo.py` |
| How-to guides | `docs/node-definition-integration-guide.md`, `docs/node-definition-mapping-guide.md` |
