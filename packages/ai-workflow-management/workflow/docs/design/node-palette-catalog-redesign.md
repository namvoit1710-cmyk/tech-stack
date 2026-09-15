# Node Palette & Catalog Redesign

**Status:** Proposal
**Date:** 2026-04-19
**Scope:** workflow-control-plane-service, worker-executor-service, worker-sdk, frontend workflow builder
**Related:** `frontend/10-node-palette-and-definitions.md` (current state), `business-node-guide.md`

---

## 1. Summary

Consolidate the node catalog around **one canonical DTO**, **two APIs** (list + detail), **two taxonomy axes** (`node_type` + `category`), and **three versioning axes** (worker build, node spec, business node version). This removes ~60% of the fields and fallbacks currently drifting between the four layers (worker SDK → executor → control-plane → frontend), without requiring any change to the orchestration engine's closed set of 13 node types.

### 1.1 Non-negotiable design principle — keep the auto-form schema

The frontend workflow builder already renders property panels from the project's **auto-form schema** (`field_control`, `field_wrapper`, `wrapper_props`, `control_props`, `condition`, `rules`). This design **keeps that schema as the single form contract**. We harden it with pydantic v2 for validation at the boundary; we do not replace it.

Specifically, this proposal **rejects**:
- JSON Schema (draft-07, draft-2020-12, OpenAPI) as a wire or storage format.
- Parallel annotation schemes like `x-ui`, RJSF, or JSONForms.
- Any two-schema split ("validate with X, render with Y").

The frontend renderer, its widgets, and its conditional-visibility logic stay exactly as they are today. All changes are backend-side: typing, validation, versioning, packaging.

---

## 2. Problem Statement

### 2.1 Current pain points

| # | Issue | Evidence |
|---|---|---|
| 1 | Same concept modeled 4 times with field drift | `worker-sdk/.../node_type_definition.py:9-30` vs `worker-executor-service/.../worker_registration.py:1-46` vs `list_node_palette_use_case.py:176-194` vs implicit TS |
| 2 | Two schema formats coexist (`AutoFormField` vs raw dict) | `control_flow_schemas.py` vs worker registrations |
| 3 | Builtins hardcoded in CP | `BUILTIN_NODES` list + `SCHEMAS` dict |
| 4 | Palette payload carries every node's full schema on first load | Scales with schema complexity, not node count |
| 5 | No per-node-type versioning | `NodeTypeDefinition.version` versions the whole worker |
| 6 | `node_class` / `provider` duplicate or drift from `node_type` | Fallback at `list_node_palette_use_case.py:165` |
| 7 | Health filtering silently removes nodes during transient outages | `_fetch_worker_nodes:152` |
| 8 | Business nodes lack versioning — mutable rows | `NodeDefinition` has no `version` field |

### 2.2 Goals

- One canonical `NodeDefinition` DTO shared across SDK, executor, control-plane, frontend.
- Fast palette loads that don't scale with schema size.
- Per-node-type versioning decoupled from worker release cadence.
- Immutable, versioned business nodes.
- Data-driven palette grouping (product-editable, not code-editable).

### 2.3 Non-goals

- Changing the engine's set of 13 `NodeType`s.
- Changing orchestration or dispatch semantics.
- Auto-migrating deprecated workflows (always user-consented).

---

## 3. Canonical `NodeDefinition` DTO

Defined once in a shared package `packages/node-contracts/`, imported by SDK and control-plane, codegen'd to TS for the frontend. **All models are pydantic v2** — validation happens at the boundary (worker registration, CP aggregation, detail endpoint), not implicitly in the frontend.

```python
# packages/node-contracts/node_contracts/node_definition.py
from pydantic import BaseModel, Field
from typing import Literal, Optional
from .form_schema import FormField   # see §3.1

class NodePort(BaseModel):
    id: str
    label: str
    required: bool = False
    description: str = ""
    data_type: Optional[str] = None

class NodeCapability(BaseModel):
    domain: str
    action: str

class NodeVisibility(BaseModel):
    tenants: Optional[list[str]] = None
    roles: Optional[list[str]] = None
    feature_flags: Optional[list[str]] = None

class NodeRuntime(BaseModel):
    provider: Literal["builtin", "worker", "business"]
    worker_instances: int = 0
    availability: Literal["available", "degraded", "offline"] = "available"
    last_seen_at: Optional[str] = None

class NodeDefinitionSummary(BaseModel):
    """Lightweight palette tile — no schemas."""
    id: str                   # "builtin:condition", "worker:http-request@1.2", "business:<uuid>@3"
    node_type: str            # engine enum: TASK, CONDITION, LOOP, SWITCH, MERGE, …
    spec_version: str         # semver for workers/builtins; stringified int for business
    name: str
    description: str = ""
    icon: str = "Cog"
    color: str = "#3B82F6"
    category: str             # dot-path: "io.http", "ai.agents"
    subcategory: Optional[str] = None
    tags: list[str] = []
    ports: dict[Literal["in","out"], list[NodePort]]  # shape only — id+label
    deprecated: bool = False
    replaced_by: Optional[str] = None
    runtime: Optional[NodeRuntime] = None

class NodeDefinitionDetail(NodeDefinitionSummary):
    """Full record — fetched on demand for config modal and runtime validation."""
    input_schema: list[FormField]         # typed, validated on construct (§3.1)
    output_schema: list[FormField]
    ports: dict[Literal["in","out"], list[NodePort]]   # full — with data_type, description
    capabilities: list[NodeCapability] = []
    visibility: NodeVisibility = NodeVisibility()
    deprecated_message: Optional[str] = None
    docs_url: Optional[str] = None
    examples: list[dict] = []
```

**Ownership split:**
- Worker emits everything except `runtime` and `id` prefix.
- Executor prepends `worker:`, fills `runtime` (health, instance count, last_seen_at).
- Control-plane merges builtin + worker + business providers, filters by visibility.
- Frontend consumes `NodeDefinitionSummary[]` for palette, `NodeDefinitionDetail` on node open.

**Retired fields (derive or drop):**
- `node_class` — derive from `node_type` via an engine-owned table only when permissions need it.
- `provider` — read from `id` prefix; never declared.
- Loose `dict` form schemas — replaced by the typed `FormField` pydantic model in §3.1.

### 3.1 `FormField` — typed form schema (pydantic)

`input_schema` / `output_schema` are **lists of pydantic-validated `FormField` objects**, not raw dicts. This is a hardened, shared-package version of today's `AutoFormField` (`auto_form_mapper/models.py:96`). The schema survives as one artifact — the frontend renderer and backend input validator both read the same object.

**Why pydantic, not a dict:**

| Concern | Loose dict | Typed pydantic `FormField` |
|---|---|---|
| Typo in `field_control` (`"SELCT"`) | Silently ships to frontend, renders blank | Rejected at `register_worker` boundary |
| Invalid `operator` in `condition` | Frontend guesses | `ValidationError` — worker fails to start |
| `output_type` doesn't match `field_control` | Hard to diagnose | Model-level `@model_validator` rejects |
| TS types in frontend | Hand-written, drifts | Codegen from pydantic (datamodel-codegen / pydantic2ts) |
| Backward compat | Unknown fields accepted silently | `extra="forbid"` — schema changes are explicit |

**The models:**

```python
# packages/node-contracts/node_contracts/form_schema.py
from pydantic import BaseModel, ConfigDict, model_validator
from typing import Literal, Optional, Union, Any
from enum import Enum


class OutputType(str, Enum):
    STRING  = "string"
    NUMBER  = "number"
    BOOLEAN = "boolean"
    OBJECT  = "object"
    ARRAY   = "array"


class FieldControl(str, Enum):
    INPUT      = "INPUT"
    TEXTAREA   = "TEXTAREA"
    SELECT     = "SELECT"
    COMBOBOX   = "COMBOBOX"
    SWITCH     = "SWITCH"
    CODE       = "CODE"
    DATE       = "DATE"
    NUMBER     = "NUMBER"
    CHECKBOX   = "CHECKBOX"
    RADIO      = "RADIO"
    FILE       = "FILE"
    # closed set — adding one is an engine-contract release


class FieldWrapper(str, Enum):
    FORM_ITEM  = "FORM_ITEM"
    TABLE_ROW  = "TABLE_ROW"
    TABLE_CELL = "TABLE_CELL"
    FRAGMENT   = "FRAGMENT"


class ConditionOperator(str, Enum):
    EQUALS        = "="
    NOT_EQUALS    = "!="
    IN            = "in"
    NOT_IN        = "not_in"
    GREATER_THAN  = ">"
    LESS_THAN     = "<"
    CONTAINS      = "contains"
    IS_EMPTY      = "is_empty"


class ConditionAction(str, Enum):
    VISIBLE    = "visible"
    INVISIBLE  = "invisible"
    DISABLED   = "disabled"
    READONLY   = "readonly"


class ValidationMethod(str, Enum):
    REQUIRED = "required"
    PATTERN  = "pattern"
    MIN      = "min"
    MAX      = "max"
    MIN_LEN  = "minLength"
    MAX_LEN  = "maxLength"
    ENUM     = "enum"


class ValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: ValidationMethod
    value: Optional[Union[str, int, float, list[Any]]] = None
    message: Optional[str] = None


class ConditionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_key: str
    operator: ConditionOperator = ConditionOperator.EQUALS
    condition_value: Union[str, int, float, bool, list[Any]]
    action: ConditionAction = ConditionAction.INVISIBLE


class WrapperProps(BaseModel):
    """Closed shape — add fields explicitly, never catch-all."""
    model_config = ConfigDict(extra="forbid")
    label: Optional[str] = None
    required: bool = False
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    label_span: Optional[str] = None
    field_span: Optional[str] = None


class SelectOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Union[str, int, float, bool]
    label: str


class ControlProps(BaseModel):
    """Closed shape per-control; unknown fields rejected."""
    model_config = ConfigDict(extra="forbid")
    placeholder: Optional[str] = None
    readonly: bool = False
    options: Optional[list[SelectOption]] = None       # for SELECT / RADIO / COMBOBOX
    language: Optional[str] = None                     # for CODE
    min: Optional[float] = None                        # for NUMBER / DATE
    max: Optional[float] = None
    step: Optional[float] = None                       # for NUMBER
    accept: Optional[str] = None                       # for FILE
    multiple: bool = False


class FieldConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_control: FieldControl
    field_wrapper: FieldWrapper = FieldWrapper.FORM_ITEM
    wrapper_props: WrapperProps = WrapperProps()
    control_props: ControlProps = ControlProps()
    condition: Optional[ConditionConfig] = None
    rules: list[ValidationRule] = []


class FormField(BaseModel):
    """One renderable field in an input_schema or output_schema."""
    model_config = ConfigDict(extra="forbid")
    key: str
    output_type: OutputType
    field_config: FieldConfig
    fields: Optional[list["FormField"]] = None   # nested for array / object

    @model_validator(mode="after")
    def _control_matches_type(self) -> "FormField":
        """Reject combinations the frontend can't render coherently."""
        control = self.field_config.field_control
        if self.output_type == OutputType.OBJECT and self.fields is None:
            raise ValueError(f"field '{self.key}': output_type=object requires nested fields")
        if self.output_type == OutputType.ARRAY and self.fields is None:
            raise ValueError(f"field '{self.key}': output_type=array requires nested fields")
        if control == FieldControl.SELECT and not self.field_config.control_props.options:
            raise ValueError(f"field '{self.key}': SELECT requires control_props.options")
        if control == FieldControl.SWITCH and self.output_type != OutputType.BOOLEAN:
            raise ValueError(f"field '{self.key}': SWITCH requires output_type=boolean")
        if control == FieldControl.NUMBER and self.output_type != OutputType.NUMBER:
            raise ValueError(f"field '{self.key}': NUMBER control requires output_type=number")
        return self

FormField.model_rebuild()
```

**Worker-side usage** — workers construct `FormField` instances directly; failure is at import time:

```python
# worker/http-request-worker/app/.../execute_task_usecase.py
from node_contracts.form_schema import FormField, FieldConfig, FieldControl, OutputType, WrapperProps, ControlProps, SelectOption

INPUT_SCHEMA: list[FormField] = [
    FormField(
        key="url",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            wrapper_props=WrapperProps(label="URL", required=True),
            control_props=ControlProps(placeholder="https://…"),
            rules=[ValidationRule(method=ValidationMethod.REQUIRED, message="URL is required")],
        ),
    ),
    FormField(
        key="method",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            wrapper_props=WrapperProps(label="Method", required=True),
            control_props=ControlProps(options=[
                SelectOption(value="GET",  label="GET"),
                SelectOption(value="POST", label="POST"),
                SelectOption(value="PUT",  label="PUT"),
            ]),
        ),
    ),
    FormField(
        key="body",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.CODE,
            wrapper_props=WrapperProps(label="Body"),
            control_props=ControlProps(language="json"),
            condition=ConditionConfig(
                field_key="method",
                operator=ConditionOperator.IN,
                condition_value=["POST", "PUT"],
                action=ConditionAction.VISIBLE,
            ),
        ),
    ),
]
```

A typo (`FieldControl.SELCT`, `operator="=="`, unknown key in `control_props`) raises at worker startup — the worker never registers with a broken schema.

**Validation responsibilities:**

| Boundary | Validates |
|---|---|
| Worker startup | `FormField` models construct cleanly |
| `POST /workers/register` (executor) | Re-parse payload into `NodeDefinitionDetail` — reject non-conformant schemas with 400 |
| `GET /node-catalog/{id}@{v}` (CP) | Re-serialize via pydantic — drift detected at boundary |
| Workflow run input (engine) | Apply `ValidationRule[]` per field against user-provided config |
| Business node save | Same validation as worker; additionally check `locked_defaults` keys exist in `input_schema` |

**TS codegen:**
```
# CI step in node-contracts repo
datamodel-codegen --input  node_contracts/ \
                  --output frontend/src/generated/node-contracts.ts \
                  --target-python-version 3.11 \
                  --output-model-type typescript
```

Frontend imports `FormField`, `FieldControl`, `ConditionConfig` directly. A renamed Python field fails the frontend build — never ships silently.

**What this replaces:**
- `workflow-control-plane-service/app/layer4_frameworks/auto_form_mapper/models.py` — move to shared package, upgrade from `@dataclass` to `BaseModel`, add `model_validator` rules and `extra="forbid"`.
- Loose dict / AutoFormField-dict hybrid on the wire — gone; one typed contract.

**What this explicitly does NOT introduce:**
- **No JSON Schema** (draft-07, draft-2020-12, OpenAPI, etc.) on the wire or in storage.
- **No `x-ui` extension annotations**, no RJSF/JSONForms-style parallel schema.
- **No second schema** for validation vs. rendering — `FormField` + its `ValidationRule[]` is both.

The frontend already has a working auto-form renderer built around `field_control`, `field_wrapper`, `wrapper_props`, `control_props`, `condition`, and `rules`. This design preserves that exact shape (field-for-field) and hardens it with pydantic. **No frontend rewrite, no renderer swap, no schema translator** — the control-plane keeps emitting auto-form schemas the UI already understands; the only change the frontend sees is that the objects now come from generated TS types instead of handwritten ones.

---

## 4. Two APIs: List + Detail

### 4.1 Endpoints

| Endpoint | Purpose | Payload | Cache |
|---|---|---|---|
| `GET /node-catalog` | Palette tiles | `NodeDefinitionSummary[]` + `category_tree` | ETag, ~30s TTL |
| `GET /node-catalog/{id}@{version}` | Full detail for config modal | `NodeDefinitionDetail` | ETag, per-version immutable |
| `GET /node-catalog/details?ids=a@1,b@2` | Batch detail (workflow load) | `NodeDefinitionDetail[]` | ETag |
| `GET /node-catalog/{id}/versions` | History for a definition family | `NodeDefinitionSummary[]` | short TTL |

### 4.2 List response shape

```json
{
  "definitions": [ /* NodeDefinitionSummary */ ],
  "category_tree": {
    "control_flow": { "label": "Control Flow", "order": 1, "icon": "GitBranch",
      "children": { "branching": {"label":"Branching","order":1}, "iteration": {"label":"Iteration","order":2} } },
    "io":          { "label": "I/O", "order": 2, "children": {...} },
    "ai":          { "label": "AI", "order": 3, "children": {...} },
    "business":    { "label": "Business", "order": 99 }
  },
  "generated_at": "2026-04-19T10:00:00Z",
  "etag": "W/\"abc123\""
}
```

### 4.3 Frontend fetch strategy

| Trigger | Action |
|---|---|
| Workflow builder open | `GET /node-catalog` (with `If-None-Match`) |
| User drags node from palette | Prefetch `GET /node-catalog/{id}@{version}` on drag-start |
| User opens node config modal | Read from prefetch cache; fetch if miss |
| Workflow loaded from storage | `GET /node-catalog/details?ids=…` for all used (id, version) pairs |
| Worker registers/deregisters | Executor publishes `catalog.changed` → CP invalidates cache → Socket.IO broadcasts `node_palette.updated` → frontend refetches list |

### 4.4 Why split

- Palette payload scales with node count, not schema complexity.
- Detail endpoint is per-version immutable → cache forever (subject to ETag).
- List endpoint changes only on register/deregister/deprecate → bust once, clients revalidate.

---

## 5. Taxonomy: `node_type` vs `category`

Two orthogonal axes. Do not conflate.

| Axis | Owner | Shape | Purpose | Change cost |
|---|---|---|---|---|
| `node_type` | Orchestration engine | Closed enum (13 values today) | Dispatch path — `condition_handler`, `loop_handler`, `task_dispatcher`, … | Engine release |
| `category` | Product | Open dot-path string | Palette grouping, search filters | Config/data edit |

### 5.1 Engine-owned `node_type` set

```
CONTROL_FLOW: CONDITION, SWITCH, LOOP, PARALLEL, MERGE, WORKFLOW, COMPUTE
TASK:         TASK
PENDING:      INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD
SYSTEM:       TRIGGER
```

### 5.2 Product-owned `category` tree

```
control_flow
├── branching         → Condition, Switch
├── iteration         → Loop
├── parallel          → Parallel, Merge
└── subworkflow       → Workflow, Group
io
├── http              → HTTP Request
├── files             → File Upload, Data Edit
└── messaging         → (future)
ai
├── agents            → Agent Runner
├── prompts           → Prompt Template
└── rag               → Retriever, Reranker
data
├── mapping           → Mapping Data, Compute
└── transform         → JSON Transform
human
├── input             → Input, Human Action
└── approval          → Approval Gate
business               → tenant-defined
system
└── trigger           → Trigger
```

### 5.3 Rule: what forces a frontend change

| Change | Frontend work? |
|---|---|
| New worker using existing `node_type=TASK` | **None** |
| New `category` or `subcategory` | **None** — data only |
| New field in a node's `input_schema` | **None** — existing auto-form renderer picks it up |
| `spec_version` bump (minor/patch) | **None** — renderer adapts, users see "update available" |
| New `node_type` reusing an existing canvas shape | **None** |
| New `node_type` with novel canvas semantics (e.g. 5-way router) | **Yes** — shape renderer update |

---

## 6. Versioning — Three Axes

Keep separate. Each answers a different question.

| Axis | Field | Example | Bumped by | Purpose |
|---|---|---|---|---|
| Worker build | `WorkerRegistration.build_version` | `2026.04.19-abc123` | CI on deploy | Process identity for debug/metrics |
| Worker node spec | `NodeDefinitionDetail.spec_version` | `1.2.0` (semver) | Worker code change | Contract compatibility for workflows |
| Business node | `NodeDefinitionDetail.version` | `3` (monotonic int) | User save (publish) | Track human edits to a template |

### 6.1 Semver rules for worker node spec

- **MAJOR** — breaking `input_schema` change (remove/rename field, tighten type, remove port)
- **MINOR** — backward-compatible addition (new optional field, new output)
- **PATCH** — description/icon/tag/docs only

### 6.2 Pin on placement

Workflows pin `(definition_id, spec_version)` at design time:

```json
{
  "node_id": "n_7",
  "definition_id": "worker:http-request",
  "spec_version": "1.2.0",
  "config": { "url": "...", "method": "GET" }
}
```

### 6.3 Runtime resolution

Executor maintains `(worker_type, spec_version) → instance pool` and picks a worker where:
- Major version matches the pinned major.
- Minor version ≥ pinned minor.

This enables zero-downtime schema evolution: `http-request@1.2` and `http-request@1.3` run side by side; workflows migrate at their own pace.

### 6.4 Palette visibility

Default view: **highest non-deprecated spec_version per `(category, short_name)`**.
"Show legacy versions" toggle: includes deprecated and older minors, greyed with `replaced_by` pointer.

### 6.5 Business-node versioning (see §7.3)

Immutable rows per save. Primary key `(id, version)`. Workflows pin `(id, version)`.

---

## 7. Business Nodes (User-Defined)

### 7.1 Resolution model (unchanged, working correctly today)

Business nodes do not introduce new `node_type`s — they **pre-fill** one of the engine's 13 primitives.

```
NodeDefinition (business)
├── base_type: NodeType         ← closed engine enum
├── base_worker_id: str?        ← required when base_type = TASK
├── base_worker_spec_version    ← NEW: pin worker contract at publish
├── locked_defaults: dict       ← fields end-user cannot override
├── input_schema / output_schema
├── input_mapping / output_mapping
├── parent_definition_id + parent_version  ← NEW: pin inheritance
├── icon / color / tags / ports
├── version: int                ← NEW
├── status                       ← NEW: draft | published | deprecated | archived
└── is_latest: bool              ← NEW
```

Resolver (`NodeDefinitionResolver`) walks `parent_definition_id` up to `MAX_INHERITANCE_DEPTH=10`, merges root→leaf, emits `ResolvedNodeDefinition`. Engine dispatches on `base_type` and never needs to know business nodes exist.

### 7.2 Wrapping by `base_type`

All 13 `NodeType`s are legal `base_type` values. Value varies:

| Tier | `base_type` | Lock surface |
|---|---|---|
| **High value — show in UI** | TASK | worker_id + partial config |
| | CONDITION | expression |
| | SWITCH | cases + expression |
| | LOOP | loop_type, collection path, parallelism |
| | WORKFLOW | workflow_id + binding |
| | INPUT | input_schema |
| | HUMAN_ACTION | action schema + instructions |
| | COMPUTE | expression |
| **Medium — allow, guard** | TRIGGER | path/topic (RBAC-gated) |
| | DATA_EDIT | artifact schema |
| | FILE_UPLOAD | mime types, limits |
| | PARALLEL | fan-out, fail strategy |
| **Low — allowed in data, hidden from UI** | MERGE | nothing meaningful |

### 7.3 Versioning & lifecycle

Immutable rows keyed by `(id, version)`:

```
draft ──publish──▶ published ──deprecate──▶ deprecated ──archive──▶ archived
  │                    │                         │
  editable             immutable                 immutable
  never pinnable       pinnable                  existing workflows run,
                                                 palette hides for new placements
```

| State | Palette visible | Pinnable | Runs |
|---|---|---|---|
| draft | only in editor | no | no |
| published | yes | yes | yes |
| deprecated | "show legacy" toggle | existing only | yes |
| archived | no | no | **no — blocks run, prompts migration** |

Every save creates `version+1` as a new row. No update-in-place.

### 7.4 Parent pinning

On publish, snapshot the current `parent.version` into `parent_version`. Children do not float to "latest parent" — editor surfaces "parent updated to v4, update this node?" banner; user consents explicitly.

### 7.5 Hardened validation

Extend `NodeDefinition.validate()` at `entities/node_definition.py:42`:

```python
def validate(self) -> list[str]:
    errors = []
    # ... existing checks ...

    # 1. Port topology matches base_type
    expected = EXPECTED_PORT_TOPOLOGY[self.base_type]
    if not expected.matches(self.ports, self.locked_defaults):
        errors.append(f"ports incompatible with base_type={self.base_type}")

    # 2. locked_defaults keys exist on base schema
    base_schema = get_base_schema(self.base_type, self.base_worker_id)
    unknown = set(self.locked_defaults) - set(base_schema.fields())
    if unknown:
        errors.append(f"locked_defaults has unknown fields: {unknown}")

    # 3. Base-type-specific rules
    if self.base_type == NodeType.MERGE and self.locked_defaults:
        errors.append("MERGE has nothing to lock")
    if self.base_type == NodeType.TASK and not self.base_worker_id and not self.parent_definition_id:
        errors.append("base_worker_id is required when base_type is TASK")

    return errors
```

### 7.6 Palette grouping for business nodes

Group by `base_type` (the engine primitive they wrap), not only `category`:

```
▼ Business — Tasks          (base_type = TASK)
   ◆ Slack Alert, PagerDuty Escalation
▼ Business — Rules          (CONDITION / SWITCH)
   ◆ Is Premium Customer?, Route by Region
▼ Business — Sub-workflows  (WORKFLOW)
   ◆ Contract Approval
▼ Business — Human Steps    (INPUT / HUMAN_ACTION / DATA_EDIT)
   ◆ Collect Shipping Address, Manager Approval
```

---

## 8. Frontend Rendering Guide

### 8.1 Palette tile

```
┌ icon  Name                     ─┐
│       category · tag1 · tag2    │
│       spec_version  availability│
└─────────────────────────────────┘
```

- `icon` → icon registry lookup (lucide/hero)
- `color` → accent/border
- `name`, `tags` → searchable text
- `deprecated` → strikethrough + tooltip with `replaced_by`
- `runtime.availability` → status dot (green/amber/grey)

### 8.2 Canvas node shape

Branch on `node_type` only:
- `TASK` → rectangle with 1 in / 1 out (+ optional `error`)
- `CONDITION` → diamond with 1 in / `true`+`false` out
- `SWITCH` → hexagon with 1 in / N cases + `default`
- `LOOP` → rectangle with 1 in / `body` + `continuation`
- `PARALLEL`/`MERGE` → dedicated fan-out / fan-in glyph
- `INPUT`/`HUMAN_ACTION`/`DATA_EDIT`/`FILE_UPLOAD` → pill with pause indicator
- `TRIGGER` → entry-point glyph

### 8.3 Config modal

Driven by `input_schema: list[FormField]` (typed pydantic → TS). Frontend has **one** renderer that switches on `field_config.field_control`:

```ts
// simplified — see generated/node-contracts.ts for full types
function renderField(field: FormField, value, onChange) {
  const { field_control, wrapper_props, control_props, condition, rules } = field.field_config;
  if (condition && !matches(condition, formState)) return null;  // visibility
  switch (field_control) {
    case "INPUT":    return <TextInput    {...wrapper_props} {...control_props} value={value} onChange={onChange}/>;
    case "TEXTAREA": return <TextArea     {...wrapper_props} {...control_props} value={value} onChange={onChange}/>;
    case "SELECT":   return <Select       {...wrapper_props} options={control_props.options} value={value} onChange={onChange}/>;
    case "SWITCH":   return <Switch       {...wrapper_props} value={value} onChange={onChange}/>;
    case "CODE":     return <CodeEditor   {...wrapper_props} language={control_props.language} value={value} onChange={onChange}/>;
    // …
  }
}
```

No per-node rendering logic. Adding a new `FieldControl` enum value is the only change that touches the renderer — and it's a typed addition, not a string comparison.

**Validation** runs client-side from `rules[]` and is re-applied server-side by the engine. One rule definition, two enforcement points.

### 8.4 Version pinning UX

When a workflow loads:
- Pinned `spec_version` matches latest → normal.
- Newer minor available → amber dot, "Update available" chip.
- Newer major available, compatible mapping exists → amber dot, "Update to 2.0" opens diff dialog.
- Pinned version deprecated → amber dot, nudge to upgrade.
- Pinned version archived or worker no longer offers compatible spec → **red dot, blocks run until migrated.**

Never auto-migrate.

---

## 9. Contract Surface Changes

### 9.1 Additions

| Location | Field | Why |
|---|---|---|
| `NodeDefinition` (business) | `version: int`, `status`, `is_latest`, `parent_version`, `base_worker_spec_version` | Immutable versioning, parent pinning |
| `NodeTypeDefinition` (SDK) | `spec_version: str`, `category: str` | Per-node-type semver + product-owned grouping |
| Palette list response | `category_tree`, `etag`, `generated_at` | Data-driven UX + cache |
| Shared package `node-contracts` | `NodeDefinitionSummary`, `NodeDefinitionDetail`, `FormField` + supporting enums (all pydantic v2) | Unified DTO + typed form schema validated at every boundary |

### 9.2 Deprecations

| Field | Reason | Removal |
|---|---|---|
| `node_class` on wire | Derivable from `node_type` | After frontend migration |
| `provider` as declared field | Derivable from `id` prefix | After frontend migration |
| `auto_form_mapper.models` (dataclass `AutoFormField`, `FieldConfig`, …) | Replaced by pydantic `FormField` in shared `node-contracts` package — same field shape, stricter validation | After worker SDK + CP emit pydantic `FormField` |
| Loose `dict` form schemas anywhere | Replaced by typed `list[FormField]`; `extra="forbid"` everywhere | Same release as above |
| Full schemas in palette list | Moved to detail endpoint | After prefetch path lands |

### 9.3 Preserved

- All 13 `NodeType` values and their semantics.
- `NodeDefinition.base_type` + `base_worker_id` pattern.
- `parent_definition_id` inheritance with `MAX_INHERITANCE_DEPTH=10`.
- `NodeDefinitionResolver` merge algorithm.
- `locked_defaults` as the locking mechanism.

---

## 10. Migration Plan

Non-breaking rollout. New fields are additive; old fields are accepted during transition.

| Week | Change | Breaking? |
|---|---|---|
| 1 | Publish `node-contracts` package; pydantic models; TS codegen | No |
| 2 | CP serializes new shape alongside old; frontend reads new shape behind flag | No |
| 3 | Workers emit `spec_version`, `category`. Executor accepts both old + new registration payloads. | No |
| 4 | Builtins moved from hardcoded `BUILTIN_NODES` dicts to `NodeDefinition` instances in `builtin_provider.py`. Same shape as workers. | No |
| 5 | Business nodes: migration — each existing row becomes `(id, version=1, status=published, is_latest=true)`. Add `base_worker_spec_version` best-effort ("latest at time of migration"). | No (one-time data migration) |
| 6 | Split APIs: `/node-catalog` (summary) + `/node-catalog/{id}@{version}` (detail). Frontend prefetch on drag-start. | No (old `/node-palette` kept during transition) |
| 7 | Flip frontend flag; stop emitting full schemas from list endpoint. | Yes — schema move |
| 8 | Remove `node_class`, `provider` from wire. Retire `AutoFormField` wire format (keep internally if needed). | Yes — drift cleanup |
| 9 | Retire `/node-palette` endpoint. | Yes — old API |

Any step can be paused or reverted independently; weeks 7–9 are the only breaking ones and each has its own flag.

---

## 11. Validation Rules (Engine-Owned)

Centralized in `packages/node-contracts` — not per-service.

```python
EXPECTED_PORT_TOPOLOGY: dict[NodeType, PortTopology] = {
    NodeType.CONDITION: PortTopology.fixed(in_=1, out=["true","false"]),
    NodeType.SWITCH:    PortTopology.derived_from="cases",
    NodeType.LOOP:      PortTopology.fixed(in_=1, out=["body","continuation"]),
    NodeType.PARALLEL:  PortTopology.derived_from="branches",
    NodeType.MERGE:     PortTopology.fixed(in_="many", out=["out"]),
    NodeType.TASK:      PortTopology.worker_declared,
    NodeType.INPUT:     PortTopology.fixed(in_=1, out=["out","cancelled"]),
    NodeType.HUMAN_ACTION: PortTopology.fixed(in_=1, out=["approved","rejected"]),
    NodeType.DATA_EDIT: PortTopology.fixed(in_=1, out=["out","cancelled"]),
    NodeType.FILE_UPLOAD: PortTopology.fixed(in_=1, out=["out","cancelled"]),
    NodeType.WORKFLOW:  PortTopology.fixed(in_=1, out=["out","error"]),
    NodeType.COMPUTE:   PortTopology.fixed(in_=1, out=["out"]),
    NodeType.TRIGGER:   PortTopology.fixed(in_=0, out=["out"]),
}
```

Applied in:
- Business node validate on save.
- Worker registration validate on receive.
- Workflow validate before run.

---

## 12. Open Questions

1. **`FieldControl` extension policy** — who owns the enum? Frontend renderer defines the set; adding a value requires both a pydantic release and a renderer case. Tag as a "contract release" in `node-contracts` CHANGELOG.
2. **TS codegen tool** — `datamodel-codegen` vs `pydantic2ts`? `pydantic2ts` preserves discriminated unions better; `datamodel-codegen` is more actively maintained. Spike both, pick one.
3. **Category tree storage** — shipped with CP (static file) or in a DB table (editable via admin UI)? Start **static**; move to DB when product asks for live edits.
4. **Business node publish permissions** — single "editor" role, or per-category governance? Out of scope; decide with security/product.
5. **Trigger creation as business nodes** — should any tenant user be able to create `base_type=TRIGGER` business nodes? Likely admin-only.
6. **Deprecation policy** — how long does a deprecated `spec_version` stay runnable? Suggest configurable per-worker, default **90 days** after next version publish.

---

## 13. Success Criteria

- New worker deploy adds a node to the palette with **zero** control-plane or frontend code changes.
- `GET /node-catalog` response stays under **50KB** regardless of schema complexity.
- Workflow pinned to `spec_version=1.2.0` continues to run after worker upgrades to `1.3.0`.
- Adding a new `category` requires no code release.
- Business node publish creates an immutable row; editing a published node produces `version+1`.
- Palette shows deprecated nodes clearly and offers one-click migration for compatible `spec_version` bumps.
- **A typo in a worker's `input_schema` (bad `field_control`, unknown `control_props` key, missing `options` on a SELECT) fails worker startup — never reaches the frontend.**
- **Renaming a field in `FormField` breaks the frontend build** (via codegen'd TS), not a production render.

---

## 14. References

- Current state: `docs/design/frontend/10-node-palette-and-definitions.md`
- Business node guide: `workflow-control-plane-service/docs/business-node-guide.md`
- Workflow schema: `workflow-control-plane-service/docs/workflow-schema-reference.md`
- Existing resolver: `workflow-control-plane-service/app/layer1_domain/services/node_definition_resolver.py`
- Existing palette use case: `workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py`
- Worker SDK node type entity: `worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py`
