# NodeSpec — Single Source of Truth for Node Configuration

**Status:** Proposed (design only — no implementation yet)
**Date:** 2026-06-12
**Scope:** workflow-control-plane-service (builtin node types, worker nodes, business node definitions)

---

## 1. Motivation: the drift inventory

Node configuration — the design-time settings each node type accepts, how they are
rendered as FE forms, split from runtime bindings, validated, and read by handlers —
is currently declared in **five independent hand-maintained catalogs**:

| # | Catalog | Where | Declares |
|---|---------|-------|----------|
| 1 | AutoForm `SCHEMAS` (~870 lines) | `app/layer2_application/features/list_node_palette/use_cases/control_flow_schemas.py` | Per-node-type form fields served to the FE palette |
| 2 | `_CONFIG_KEYS` | `app/layer1_domain/entities/workflow.py:32` | Which `parameters` keys go to engine `config` vs `input_binding` |
| 3 | `_CONFIG_TYPE_MAP` | `app/layer1_domain/entities/workflow.py:58` | Which config keys get number/boolean coercion |
| 4 | `NodeTypeDescriptor` registry | `app/layer1_domain/value_objects/node_type_registry.py` | Ports, icon, color, node_class, min/max out ports |
| 5 | Handler reads | `app/layer2_application/services/node_handlers/*.py` (18 handlers) + `services/loop_driver/event_driven_loop_driver.py` | What keys are ACTUALLY read at runtime |

Nothing enforces agreement between them. Verified drift defects:

| Defect | Evidence |
|---|---|
| **CONDITION/SWITCH dual-source**: form field `data` (array of `{key, operations, value}`) lands in `input_binding`; `_CONFIG_KEYS` says `expression`; handlers support BOTH (structured precedence, legacy fallback) | `condition_handler.py:140-154`, `switch_handler.py:178-188,317` |
| **MERGE duplicate keys + vocabulary mismatch**: handler reads `merge_strategy OR wait_strategy` and `wait_count OR n_required`; form values are `wait_all/wait_any/wait_n`, handler vocabulary is `all/any/n_of_m` | `merge_handler.py:42-62` |
| **TRIGGER dead fields**: form has `webhook_path` + `event_filter` but `_CONFIG_KEYS["TRIGGER"]` omits them → silently routed to `input_binding`, never read; webhook routing actually uses `workflow.routing_path` | `workflow.py:41`, `control_flow_schemas.py:336-367` |
| **PARALLEL dead fields / missing field**: handler reads `branches` (not in the form); form's `max_concurrency`/`fail_strategy` are read by nobody | `parallel_handler.py:44`, `control_flow_schemas.py:223-238` |
| **WORKFLOW key collision + shape mismatch**: config key literally named `input_binding` (collides with the engine-level `WorkflowNode.input_binding` field); form supplies an array of `{param_name, param_value}` while the handler expects a dict | `workflow_handler.py:54-64`, `control_flow_schemas.py:406-409` |
| **LOOP duplicate keys**: driver reads `collection OR items_expression` | `event_driven_loop_driver.py:818` |
| **DATA_EDIT undeclared key**: handler reads `config["columns"]`, which no catalog declares — `split_parameters` can never route it to `config`, so the read only works for programmatically built nodes | `data_edit_handler.py:256` |
| **Internal state smuggled in config**: `config["_node_definition"]` and `config["_node_internals"]` snapshots written at save time, filtered out of FE reads by a `_`-prefix convention | writers in create/update workflow use cases; readers at `task_handler.py:137,166`, `schema_providers.py:117-125`, `orchestration_engine.py:954,962`, `complete_task_use_case.py:161`, `get_node_execution_use_case.py:112`, `get_variable_suggestions_use_case.py:530` |
| **No save-time validation**: only FE-side `required` rules; unknown keys, invalid enum values, type mismatches, and malformed expressions surface only as runtime failures | `_coerce_config_value` (`workflow.py:69`) silently returns the original value on failure |
| **Inverted visibility conditions**: conditional form fields use `NOT_EQUALS` "hide-when" semantics, fragile to author | `control_flow_schemas.py` throughout |

The root cause of every defect is the same: **no single source of truth**. Adding a
config field correctly today requires touching 3-5 files that don't reference each
other; missing one produces silent misrouting rather than an error.

## 2. Goals / non-goals

**Goals**

1. One declarative **NodeSpec** per node type, from which the form schema, the
   config/binding split, type coercion, save-time validation, and typed handler
   access are all *derived*. Drift becomes structurally impossible.
2. **Clean break**: canonical key names and vocabularies only. No dual-read shims,
   no legacy fallbacks in handlers. A one-time normalization migrates stored data.
3. Save-time validation with a precise, FE-consumable error surface.
4. Worker and business nodes participate in the same model (runtime-built specs).

**Non-goals**

- The FE-facing **API payload shapes do not change**: palette/catalog response
  structure, the flat `parameters` dict on nodes, and the runtime `/schema`
  endpoint contract all stay structurally as-is. (Specific *keys/values* inside
  `parameters` change for MERGE/WORKFLOW — see §10.)
- No change to variable-resolution semantics (`{{...}}`, `$node.output.x`).
- No change to dispatch/execution architecture (handler ABC tree, shapes,
  SchemaProvider plugin point all remain).
- Fixing `definition_snapshot` staleness (refresh-on-resave semantics retained;
  dispatch-time resolution is an open question, §11).

## 3. The NodeSpec model

New layer-1 package:

```
app/layer1_domain/node_spec/
├── __init__.py            # public surface: NodeSpec, get_spec, BUILTIN_SPECS, parse_config
├── model.py               # NodeSpec, ConfigField, InputField, OutputField, Choice, VisibleWhen
├── registry.py            # BUILTIN_SPECS + get_spec(node_type); absorbs node_type_registry
├── form_renderer.py       # NodeSpec -> list[AutoFormField dicts] (uses auto_form_mapper)
├── parameter_mapper.py    # spec-driven split/merge/coerce (replaces _CONFIG_KEYS/_CONFIG_TYPE_MAP)
├── validation.py          # SpecViolation + validate_node(spec, node)
└── specs/                 # one module per builtin type: spec + typed config side by side
    ├── condition.py  ├── switch.py  ├── loop.py  ├── parallel.py  ├── merge.py
    ├── trigger.py    ├── workflow_node.py  ├── compute.py  ├── input.py
    ├── human_action.py  ├── data_edit.py  ├── file_upload.py  ├── output.py
    ├── approval_flow.py ├── set_variable.py  └── task.py
```

### 3.1 Core dataclasses (`model.py`)

```python
class FieldType(str, Enum):
    STRING = "string"; NUMBER = "number"; BOOLEAN = "boolean"
    OBJECT = "object"; ARRAY = "array"; JSON = "json"     # JSON = textarea holding parseable JSON

class Widget(str, Enum):
    TEXT = "text"; TEXTAREA = "textarea"; NUMBER = "number"; SELECT = "select"
    SWITCH = "switch"; ARRAY_TABLE = "array_table"; ARRAY_INLINE = "array_inline"
    OBJECT_EDITOR = "object_editor"; TAB = "tab"

@dataclass(frozen=True)
class Choice:
    value: str          # canonical stored value (what the handler sees)
    label: str          # FE display label

@dataclass(frozen=True)
class VisibleWhen:
    field: str                      # sibling config field key
    equals: str | tuple[str, ...]   # POSITIVE semantics: "show when field == value"

@dataclass(frozen=True)
class ConfigField:
    key: str
    type: FieldType
    label: str
    required: bool = False           # enforced only when visible (per visible_when)
    default: Any = None
    choices: tuple[Choice, ...] = () # non-empty => enum-validated
    expression: bool = False         # value may contain {{...}} / $node refs
    visible_when: VisibleWhen | None = None
    widget: Widget | None = None     # None => inferred from type
    placeholder: str = ""
    description: str = ""
    item_fields: tuple["ConfigField", ...] = ()  # element shape for ARRAY fields
    internal: bool = False           # engine-consumed, never rendered in the form
    store_as: str | None = None      # value transform at split time, e.g. "dict"
                                     # (array of {param_name, param_value} rows -> dict)

@dataclass(frozen=True)
class InputField:                    # runtime data binding -> WorkflowNode.input_binding
    key: str
    type: FieldType = FieldType.STRING
    label: str = ""
    required: bool = False
    expression: bool = True          # bindings are expressions by default
    placeholder: str = ""

@dataclass(frozen=True)
class OutputField:
    key: str
    type: FieldType
    label: str

@dataclass(frozen=True)
class NodeSpec:
    node_type: NodeType
    name: str                        # "Merge"
    description: str
    category: str                    # "control_flow.concurrency"
    runtime: str                     # "engine" | "pending" | "worker"
    node_class: NodeClass
    icon: str
    color: str
    in_ports: tuple[Port, ...] = ()
    out_ports: tuple[Port, ...] = ()
    min_out_ports: int | None = None
    max_out_ports: int | None = None
    config_fields: tuple[ConfigField, ...] = ()  # design-time engine config
    input_fields: tuple[InputField, ...] = ()    # runtime data bindings
    output_fields: tuple[OutputField, ...] = ()
    requires_one_of: tuple[tuple[str, ...], ...] = ()  # cross-field exclusivity rules
    spec_version: str = "1.0.0"
```

Design decisions baked into the model:

- **`NodeTypeDescriptor` is folded in.** `NodeSpec` carries ports/icon/color/
  node_class/min-max out ports; `node_type_registry.py` is deleted and
  `node_spec/registry.py` serves the same data via `get_spec(node_type)`. Both
  are frozen layer-1 statics — keeping two registries would recreate the drift
  problem one level up.
- **Config vs input is explicit per field**, not inferred: `config_fields` route
  to `WorkflowNode.config`, `input_fields` to `WorkflowNode.input_binding`. The
  split function is derived (§4.2), so `_CONFIG_KEYS` disappears.
- **`visible_when` uses positive semantics** ("show when `trigger_type ==
  schedule`"). The form renderer inverts to the FE's existing `NOT_EQUALS`
  hide-when `ConditionConfig`, so the emitted AutoForm dict is unchanged.
  Validation reuses the same predicate to make `required` conditional.
- **`expression: bool`** marks fields whose values may contain templates;
  save-time validation runs expression-syntax checks only on those, enum
  validation is skipped when the value is a template, and the
  variable-suggestions feature can target exactly these fields.
- **`internal: bool`** declares engine-consumed config that has no form field
  (e.g. PARALLEL `branches`). Today such keys are invisible; under NodeSpec they
  are first-class — declared, validated, just not rendered.

### 3.2 Worked example: MERGE (`specs/merge.py`)

Canonical vocabulary = the handler's (`all` / `any` / `n_of_m`); the form labels
keep the friendly names. This deletes both key fallbacks and the strategy map
from `merge_handler.py`.

```python
MERGE_SPEC = NodeSpec(
    node_type=NodeType.MERGE,
    name="Merge",
    description="Waits for parallel branches to complete before continuing.",
    category="control_flow.concurrency", runtime="engine",
    node_class=NodeClass.TECHNICAL, icon="Merge", color="#10B981",
    in_ports=(), out_ports=(Port("out_default", "Output"),),
    min_out_ports=1, max_out_ports=1,
    config_fields=(
        ConfigField(
            key="merge_strategy", type=FieldType.STRING, label="Merge Strategy",
            required=True, default="all",
            choices=(Choice("all", "Wait All"),
                     Choice("any", "Wait Any"),
                     Choice("n_of_m", "Wait N")),
        ),
        ConfigField(
            key="wait_count", type=FieldType.NUMBER, label="Wait Count",
            visible_when=VisibleWhen(field="merge_strategy", equals="n_of_m"),
            placeholder="1",
        ),
        ConfigField(key="timeout_seconds", type=FieldType.NUMBER,
                    label="Timeout (seconds)", default=3600, placeholder="3600"),
        ConfigField(key="on_branch_failure", type=FieldType.STRING,
                    label="On Branch Failure", default="fail",
                    choices=(Choice("fail", "Fail"), Choice("partial", "Partial"))),
        # engine-consumed, not in the form
        ConfigField(key="inputs", type=FieldType.ARRAY, label="Inputs", internal=True,
                    item_fields=(ConfigField("source", FieldType.STRING, "Source"),
                                 ConfigField("key", FieldType.STRING, "Key"),
                                 ConfigField("required", FieldType.BOOLEAN, "Required"))),
        ConfigField(key="defaults", type=FieldType.OBJECT, label="Defaults", internal=True),
    ),
    output_fields=(
        OutputField("branches_received", FieldType.NUMBER, "Branches Received"),
        OutputField("completed", FieldType.BOOLEAN, "Completed"),
        OutputField("timed_out", FieldType.BOOLEAN, "Timed Out"),
    ),
)

@dataclass(frozen=True)
class MergeConfig:
    merge_strategy: str = "all"
    wait_count: int = 0
    timeout_seconds: int = 3600
    on_branch_failure: str = "fail"
    inputs: tuple[dict, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(cls, node: WorkflowNode) -> "MergeConfig":
        return parse_config(MERGE_SPEC, cls, node)   # shared strict parser
```

Killed by this spec: `wait_strategy`, `n_required`, the `wait_all/wait_any/wait_n`
stored vocabulary (migration maps them, §9).

### 3.3 Worked example: TRIGGER (`specs/trigger.py`)

Fixes the dead `webhook_path`/`event_filter`: all five fields are declared
CONFIG, so the derived split routes them to `config` automatically.

```python
TRIGGER_SPEC = NodeSpec(
    node_type=NodeType.TRIGGER, name="Trigger", runtime="engine",
    description="Entry point that starts workflow execution on an event or schedule.",
    category="control_flow.entry", node_class=NodeClass.TECHNICAL,
    icon="Zap", color="#F59E0B",
    in_ports=(), out_ports=(Port("out_default", "Output"),),
    config_fields=(
        ConfigField(key="trigger_type", type=FieldType.STRING, label="Trigger Type",
                    required=True, default="manual",
                    choices=(Choice("manual", "Manual"), Choice("schedule", "Schedule"),
                             Choice("event", "Event"), Choice("webhook", "Webhook"))),
        ConfigField(key="schedule_cron", type=FieldType.STRING, label="Cron Schedule",
                    placeholder="0 9 * * *", required=True,
                    visible_when=VisibleWhen("trigger_type", "schedule")),
        ConfigField(key="event_topic", type=FieldType.STRING, label="Event Topic",
                    placeholder="orders.created", required=True,
                    visible_when=VisibleWhen("trigger_type", "event")),
        ConfigField(key="event_filter", type=FieldType.STRING, label="Event Filter",
                    placeholder="$.data.priority == 'high'", expression=True,
                    visible_when=VisibleWhen("trigger_type", "event")),
        ConfigField(key="webhook_path", type=FieldType.STRING, label="Webhook Path",
                    placeholder="/hooks/my-trigger", required=True,
                    visible_when=VisibleWhen("trigger_type", "webhook")),
    ),
    output_fields=(
        OutputField("trigger_type", FieldType.STRING, "Trigger Type"),
        OutputField("triggered_at", FieldType.STRING, "Triggered At"),
        OutputField("trigger_payload", FieldType.OBJECT, "Trigger Payload"),
        OutputField("correlation_id", FieldType.STRING, "Correlation ID"),
    ),
)
```

Note `required=True` on the conditionally visible fields: validation enforces
them **only when visible** (e.g. `schedule_cron` is required iff
`trigger_type == "schedule"`), which is impossible to express today.

### 3.4 Worked example: CONDITION (`specs/condition.py`)

The structured rules array becomes a declared CONFIG field keyed `data` (keeping
the FE key avoids form churn; the *storage location* moves from `input_binding`
to `config` via migration). The free-text `expression` stays as a declared
internal alternative — not an undocumented fallback — and the spec-level
`requires_one_of` rule turns "exactly one of" into a validated invariant
instead of handler-side precedence guessing.

```python
CONDITION_SPEC = NodeSpec(
    node_type=NodeType.CONDITION, name="Condition", runtime="engine",
    description="Evaluates boolean rules and routes to true/false branches.",
    category="control_flow.branching", node_class=NodeClass.TECHNICAL,
    icon="GitBranch", color="#8B5CF6",
    in_ports=(Port("in_default", "Input"),),
    out_ports=(Port("true_branch", "True"), Port("false_branch", "False")),
    requires_one_of=(("data", "expression"),),
    config_fields=(
        ConfigField(
            key="data", type=FieldType.ARRAY, label="Data",
            widget=Widget.ARRAY_INLINE, expression=True,
            item_fields=(
                ConfigField("key", FieldType.STRING, "Key",
                            placeholder="$.input", expression=True),
                ConfigField("operations", FieldType.STRING, "Operations",
                            choices=COMPARISON_OPERATOR_CHOICES),  # shared with SWITCH
                ConfigField("value", FieldType.STRING, "Value",
                            placeholder="$", expression=True),
            ),
        ),
        ConfigField(key="expression", type=FieldType.STRING, label="Expression",
                    expression=True, internal=True),  # API-authored only
    ),
    output_fields=(OutputField("result", FieldType.BOOLEAN, "Result"),
                   OutputField("branch", FieldType.STRING, "Branch")),
)
```

`COMPARISON_OPERATOR_CHOICES` is defined once (next to `value_comparator.py`'s
canonical operator list, or generated from it) and shared by CONDITION and
SWITCH — replacing today's comment-and-test convention
("when adding an operator there, update here too").

## 4. Derivations — everything is a pure function of the spec

```
                         ┌─────────────────────────┐
                         │   NodeSpec (layer 1)    │   single source of truth
                         └────────────┬────────────┘
        ┌─────────────────┬───────────┼───────────────┬──────────────────┐
        ▼                 ▼           ▼               ▼                  ▼
 form_renderer     parameter_mapper  validation   parse_config     registry data
 (palette/catalog  (split/merge/     (save-time   (typed XxxConfig (ports, icon,
  input/output     coerce — replaces  validate_    for handlers)    color, class —
  schemas)         _CONFIG_KEYS +     node)                         replaces
                   _CONFIG_TYPE_MAP)                                NodeTypeDescriptor)
```

### 4.1 Form schema generation (`form_renderer.py`, layer 1)

`render_input_schema(spec) -> list[dict]` and `render_output_schema(spec) ->
list[dict]`, implemented on the existing `auto_form_mapper` helpers
(`text_field`, `select_field`, `array_field`, `switch_field`, `number_field`,
`ConditionConfig`). `auto_form_mapper` already lives in layer 1, so the renderer
is legitimately layer 1; layer 2 only selects and serves rendered schemas.

Mapping rules:

- `FieldType.STRING` + `choices` → `select_field` with
  `[{"id": c.value, "value": c.label}]`
- `NUMBER` → `number_field`; `BOOLEAN` → `switch_field`; explicit `widget`
  overrides win (`Widget.TEXTAREA` → `textarea_field`, …)
- `required=True` → `ValidationRule(method="required")` + `wrapperProps.required`
- `visible_when` → inverted `ConditionConfig(field_key=…, operator=NOT_EQUALS,
  condition_value=…)` so the emitted dict matches today's hide-when convention
  byte-for-byte where the field is unchanged
- `internal=True` → skipped (not rendered)
- ARRAY `item_fields` → `array_field(...)` with the current wrapper conventions
  (per-field wrapper hints where a node needs `TABLE_ROW`/`TABLE_CELL`)
- `output_fields` → today's `_readonly_*` constructions

`control_flow_schemas.py` is **deleted**. Its consumers switch to:

```python
SCHEMAS = {
    spec.node_type.value: {
        "input_schema": render_input_schema(spec),
        "output_schema": render_output_schema(spec),
    }
    for spec in BUILTIN_SPECS.values()
}
```

Consumers unchanged in shape: `list_node_palette_use_case.py` and
`providers/builtin_node_provider.py` (whose `FormField.model_validate`
round-trip becomes the structural-compat guard). **The palette/catalog HTTP
response shape does not change** — guarded by golden-file snapshot tests (§10).

### 4.2 Parameter split + coercion (`parameter_mapper.py`, layer 1)

```python
def split_parameters(spec: NodeSpec, parameters: dict) -> tuple[dict, dict]:
    by_key = {f.key: f for f in spec.config_fields}
    config = {k: coerce(by_key[k], v) for k, v in parameters.items() if k in by_key}
    binding = {k: v for k, v in parameters.items() if k not in by_key}
    return config, binding

def merge_parameters(config: dict, input_binding: dict) -> dict: ...  # contract unchanged
```

- Coercion derives from `ConfigField.type` (NUMBER/BOOLEAN coerced with today's
  forgiving rules — forgiveness is now safe because save-time validation rejects
  what coercion can't fix).
- `store_as="dict"` performs the one declared value transform: WORKFLOW's
  input-mapping rows (array of `{param_name, param_value}`) fold into a dict at
  split time, deleting the handler-side conversion.
- `WorkflowNode.__post_init__` calls
  `split_parameters(get_spec(self.node_type), self.parameters)`. Both modules
  are layer 1 — no dependency-direction change. The two module-level tables in
  `workflow.py` (`_CONFIG_KEYS`, `_CONFIG_TYPE_MAP`) and the old
  `split_parameters`/`_coerce_config_value` are deleted.
- TASK/business/worker nodes: `get_spec(NodeType.TASK)` returns the minimal TASK
  spec (config = `input_types` only); all other parameters flow to
  `input_binding`, preserving today's behavior. Runtime spec refinement for
  workers (§7) applies at catalog/validation time, **not** in `__post_init__`
  (the entity stays dependency-free).

### 4.3 Save-time validation (`validation.py`, layer 1 — pure)

```python
@dataclass(frozen=True)
class SpecViolation:
    node_id: str
    key: str
    code: str       # unknown_key | missing_required | invalid_enum
                    # | invalid_type | invalid_expression | exclusive_group
    message: str

def validate_node(spec: NodeSpec, node: WorkflowNode) -> list[SpecViolation]: ...
```

Checks (config side, hard errors):

1. **`unknown_key`** — key in `config` not declared by the spec. This catches
   the next TRIGGER-style drift *at the door*.
2. **`missing_required`** — required and currently *visible* (the `visible_when`
   predicate evaluated against sibling config values) but absent/empty.
3. **`invalid_enum`** — value not in `choices` (post-coercion; skipped when the
   value is a template and the field is `expression=True`).
4. **`invalid_type`** — NUMBER/BOOLEAN that survives coercion as the wrong type;
   ARRAY/OBJECT shape checks with `item_fields` recursion.
5. **`invalid_expression`** — for `expression=True` values containing `{{`:
   balanced-braces + reference-syntax check via a new pure
   `validate_expression_syntax(text) -> str | None` extracted from
   `VariableResolver`'s parser. Syntax only — referenced nodes may legitimately
   not have run at save time.
6. **`exclusive_group`** — `requires_one_of` (CONDITION's `data` xor
   `expression`).

### 4.4 Typed handler access (`parse_config` in `model.py`)

Each `specs/<type>.py` colocates the spec and a frozen `XxxConfig` dataclass
with `from_node()` (worked example §3.2), parsed by one shared strict
`parse_config(spec, cls, node)`:

- reads **only canonical keys** from `node.config` — no `or`-chains, no legacy
  keys, no vocabulary maps;
- missing keys take the spec default; unknown keys are ignored (already rejected
  at save time);
- applies the same deterministic coercions as `parameter_mapper` (cheap, and
  keeps handlers safe against any pre-validation data that slips through).

**Drift guard:** one parametrized unit test
(`tests/unit/test_node_spec_alignment.py`) asserts for every builtin spec:
`set(dataclasses.fields(XxxConfig)) == {f.key for f in spec.config_fields}`.
This replaces today's comment-based conventions (e.g. the operator-alignment
guard in `test_control_flow_schemas.py`).

## 5. Handler access pattern (clean break)

Before (`merge_handler.py:42-62`) — three fallbacks, a vocabulary map, manual
type checks:

```python
inputs_config = node.config.get("inputs", [])
strategy_raw = (node.config.get("merge_strategy")
                or node.config.get("wait_strategy", "all"))
strategy = _STRATEGY_MAP.get(strategy_raw, strategy_raw)
n_required = node.config.get("wait_count") or node.config.get("n_required", 0)
```

After:

```python
cfg = MergeConfig.from_node(node)
if cfg.merge_strategy == "all":     should_proceed = n_done == n_total
elif cfg.merge_strategy == "any":   should_proceed = n_done > 0
elif cfg.merge_strategy == "n_of_m": should_proceed = n_done >= (cfg.wait_count or n_total)
```

Per-handler conversion notes:

- **CONDITION / SWITCH**: structured rules move to `cfg.data` (typed, now in
  `config`); the dual-mode `_evaluate` (`condition_handler.py:117-154`) shrinks
  to "`data` present → structured, else `expression`" — both *declared*, the
  exclusivity validated at save.
- **WORKFLOW**: config key renamed `input_binding` → **`input_mapping`** (kills
  the field-name collision). Declared `FieldType.OBJECT` with
  `store_as="dict"`; the form still renders array-of-rows
  (`Widget.ARRAY_TABLE`), the split folds rows into the dict, and the handler's
  array→dict conversion (`workflow_handler.py:58-64`) is deleted.
- **PARALLEL**: spec declares `branches` (internal) only. The dead
  `max_concurrency`/`fail_strategy` form fields are **removed** until the
  handler implements them — declaring unimplemented knobs is exactly the drift
  this redesign eliminates.
- **LOOP**: canonical key is `collection`; the `items_expression` alias dies
  (migration renames). `LoopConfig` consumed by
  `event_driven_loop_driver.py:800-849`.
- **DATA_EDIT**: `columns` becomes a declared `internal` ConfigField (it is
  read at `data_edit_handler.py:256` but declared nowhere today); the
  tab-nested form fields (`file_id`, `query`, `actions`, `settings`,
  `readonly`, `custom_data_update`) are declared with their real homes —
  config or input — instead of falling through the split by omission.
- **PENDING handlers** (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD) keep their
  hook structure (`pending_base.py`); only the `node.config.get(...)` reads are
  swapped for `InputConfig.from_node(node)` etc.

All 18 handlers get this treatment; the pure-config ones (merge, workflow,
loop, trigger, compute, set_variable, parallel, switch, condition,
approval_flow, output) are mechanical.

## 6. Internal snapshots out of `config` → `definition_snapshot`

New `WorkflowNode` field (layer 1, `workflow.py`):

```python
# Resolution artifact stamped at save time by the enrichment step.
# NOT part of parameters; never round-trips to the FE.
definition_snapshot: dict[str, Any] | None = None
# shape: {"input_schema": [...], "data_mode": str, "edit_mode": str,
#         "locked_defaults": {...}, "input_mapping": {...},
#         "output_mapping": {...}, "base_worker_id": str}
```

- **Writers**: the node-definition enrichment steps in
  `create_workflow_use_case.py` and `update_workflow_use_case.py` write
  `node.definition_snapshot = {...}` instead of `config["_node_definition"]` +
  `config["_node_internals"]` (the two dicts merge into one snapshot — they
  were always written together).
- **Readers to update**: `orchestration_engine.py:954,962`,
  `task_handler.py:137,166`, `schema_providers.py:117-125`,
  `complete_task_use_case.py:161`, `get_node_execution_use_case.py:112`,
  `get_variable_suggestions_use_case.py:530`.
- `merge_parameters` drops its `_`-prefix filter (nothing internal lives in
  `config` anymore) — kept as an assertion during the migration window, then
  removed.
- Node snapshot serialization (`_node_to_snapshot`/`from_snapshot_dict` in
  `workflow.py`) carries the new field; the stored-data migration (§9) moves
  existing `config._node_definition`/`config._node_internals` into it.
- Staleness semantics unchanged (refresh on resave) — but the artifact is now
  named and documented instead of smuggled. Dispatch-time resolution is an open
  question (§11).

**PENDING runtime-schema chain simplification**: `StaticSchemaProvider`'s
4-level fallback becomes 3 — runtime (`task.output_metadata.input_schema`) →
`node.definition_snapshot["input_schema"]` → `node.config["input_schema"]`
(declared ConfigField). The JSON-string-parsing branch
(`schema_providers.py:128-134`) is deleted; the migration parses stored string
schemas once.

## 7. Worker and business nodes in the same model

Builtins get **static specs**; workers and NodeDefinitions get **spec instances
built at runtime** from registry data. New layer-2 service
`app/layer2_application/services/node_spec_resolver.py`:

```python
class NodeSpecResolver:
    def resolve(self, node: WorkflowNode) -> NodeSpec:
        # 1. builtin           -> static spec by node_type
        # 2. node_definition_id -> spec_from_node_definition(defn)
        # 3. worker_type        -> spec_from_worker(registry.get_worker_by_type(...))
```

- `spec_from_worker(worker) -> NodeSpec`: ports/icon/color from the worker
  record (as `worker_node_provider.py` does today); every worker
  `input_schema` field becomes an `InputField` (worker inputs are runtime
  bindings by definition — they dispatch in `resolved_input`); config side is
  the TASK builtin's (`input_types`); output schema → `OutputField`s. Worker
  schemas arriving as raw AutoForm dicts are parsed into spec fields
  (`spec_from_autoform(fields)`), preserving the existing lenient/strict mode
  (`strict_schema_validation` flag).
- `spec_from_node_definition(defn) -> NodeSpec`: base spec = resolve
  `defn.base_type` (or the base worker), overlay `defn.input_schema` as
  `InputField`s plus `defn.ports`/icon/color. `locked_defaults` are *not* spec
  fields — they live in `definition_snapshot` and are applied at dispatch
  exactly as today (`task_handler._apply_node_definition`).
- The three catalog providers (`builtin_node_provider.py`,
  `business_node_provider.py`, `worker_node_provider.py`) converge on
  `spec → render_input_schema/render_output_schema → NodeDefinitionDetail`,
  instead of builtins going through `SCHEMAS` while workers pass raw dicts.
- Validation severity split (a named decision): unknown **config** keys are hard
  errors everywhere; unknown **input_binding** keys on worker/business nodes are
  *warn-level* (worker schemas evolve independently; hard-failing on extra
  binding keys would break version skew).

## 8. Save-time validation wiring

**Where:** `create_workflow_use_case.execute` and
`update_workflow_use_case.execute`, immediately after nodes are constructed
(post `split_parameters`, pre enrichment/version-pinning/save). A thin layer-2
orchestrator `app/layer2_application/services/node_config_validator.py`
(`validate_workflow_nodes(workflow, spec_resolver) -> list[SpecViolation]`)
wraps the pure layer-1 `validate_node`.

**Error surface:** new `ErrorCode.NODE_CONFIG_INVALID`, returned the same way
the existing edge-reference check returns (`create_workflow_use_case.py:150`
area): result with `error_code` and `error_details` → HTTP 400. All violations
across all nodes are collected in one pass (no fail-fast) so the FE can mark
every offending field at once:

```json
{
  "error_code": "NODE_CONFIG_INVALID",
  "error_details": {
    "violations": [
      {"node_id": "merge_1", "key": "merge_strategy",
       "code": "invalid_enum",
       "message": "merge_strategy must be one of all|any|n_of_m, got 'wait_some'"},
      {"node_id": "trigger_1", "key": "schedule_cron",
       "code": "missing_required",
       "message": "schedule_cron is required when trigger_type is 'schedule'"}
    ]
  }
}
```

Follow-up (not in scope): run the same validator read-only in
`dry_run_workflow` and `test_node`.

## 9. Migration — one-time normalization (clean break)

No dual-read code ever ships. An idempotent normalization runs at deploy over
stored **workflows and run snapshots** (run snapshots matter: clean-break
handlers will read old runs' frozen node definitions; draining in-flight runs
is operationally unrealistic, so snapshots are normalized too).

Tool: `tools/normalize_node_config.py` — runnable against the configured repo
backend, idempotent, with a dry-run mode printing a per-workflow diff — plus a
migration entry under `db/migrations/`.

Transform table:

| Node type | Transform |
|---|---|
| MERGE | `wait_strategy`→`merge_strategy`; values `wait_all→all`, `wait_any→any`, `wait_n→n_of_m`; `n_required`→`wait_count` |
| TRIGGER | move `webhook_path`, `event_filter` from `input_binding` to `config` |
| CONDITION / SWITCH | move `data` from `input_binding` to `config`; if both `data` and `expression` present, keep `data`, drop `expression` (matches old precedence) |
| LOOP | `items_expression`→`collection` (drop alias if both present) |
| WORKFLOW | `config.input_binding`→`config.input_mapping`; array of `{param_name, param_value}` → dict |
| PARALLEL | drop dead `max_concurrency`/`fail_strategy` (preserved in `metadata._migration_dropped` for audit) |
| INPUT/HUMAN_ACTION | parse JSON-string `input_schema` values into structured lists (enables deleting the string-parsing branch, §6) |
| all | move `config._node_definition` + `config._node_internals` → `definition_snapshot` |
| all | re-run spec-derived `split_parameters` on `merge_parameters(config, input_binding)` to re-home any other strays; apply type coercion |
| all | run the save-time validator; **report** (not auto-fix) residual violations per workflow for manual follow-up |

**Test fixtures:** every fixture/test that builds nodes with legacy keys
(handler fallback tests, `test_control_flow_schemas.py`,
`test_builtin_node_provider.py`, …) is updated to canonical keys in the same
change; fallback-behavior tests are deleted, replaced by canonical-only-read
tests and the spec-alignment guard (§4.4). A fixture builder
`tests/helpers/node_builders.py` (`make_node(node_type, **params)` routed
through `split_parameters`) prevents tests from hand-rolling config dicts again.

**Rollout order** (phaseable within one release; the data-level break happens
once, at the migration):

1. `node_spec` package + static specs + form renderer, with golden-file tests
   proving structural parity with today's `SCHEMAS` output.
2. Spec-driven `split_parameters` + save-time validation (validation initially
   log-only for one verification pass, then enforced — within the same release).
3. Typed `XxxConfig` conversion across handlers; delete fallbacks.
4. `definition_snapshot` field + reader/writer migration.
5. Catalog providers converge on the spec renderer; delete
   `control_flow_schemas.py` and `node_type_registry.py`.
6. Run `tools/normalize_node_config.py` at deploy.

## 10. Risks & FE coordination

1. **FE coupling to the AutoForm dict shape.** Requirement: *structural*
   compatibility (same keys / fieldConfig / controlProps semantics), not
   byte-compatibility. Enforced by golden-file snapshot tests: capture today's
   `SCHEMAS` output as JSON fixtures and assert the renderer reproduces them
   field-for-field for unchanged node types. The intentional golden diffs
   (MERGE values, WORKFLOW key, PARALLEL removals, TRIGGER unchanged-form-but-
   rehomed-storage) **are the FE-coordination artifact**.
2. **Key/vocabulary renames are FE-visible through `parameters`**
   (`merge_strategy` values, WORKFLOW `input_mapping`). A FE that renders purely
   from the served schema needs no code change; any hardcoded FE handling of
   these keys must be flushed out. Stored data is normalized by the migration;
   the FE must ship in lockstep or read-tolerantly.
3. **Undiscovered producers of JSON-string `input_schema`** would break the
   simplified PENDING chain — mitigated by a migration-report scan before the
   string-parsing branch is deleted.
4. **Blast radius**: 18 handlers + loop driver, 2 save use cases, 6 snapshot
   readers, 3 catalog providers, palette use case, `workflow.py`, ~30+ test
   modules. Mitigated by the rollout order above (each phase independently
   testable) and by the golden/alignment guard tests.

## 11. Open questions

1. Should `definition_snapshot` move to **dispatch-time resolution** (killing
   staleness) in a follow-up, now that it is a named artifact?
2. Severity of unknown `input_binding` keys for worker nodes (warn vs error)
   once worker `spec_version`s are reliably pinned.
3. Are SWITCH `cases`/`default` (in `_CONFIG_KEYS` and read at
   `switch_handler.py:187-188`, but absent from the form) live anywhere in
   production data? Decide: `internal` spec fields vs dropped.
4. Does any external consumer (scheduler, webhook routing, event subscription)
   read TRIGGER `schedule_cron`/`webhook_path` from `input_binding` today and
   need a coordinated deploy? (The CP scheduler reads config — verify before
   migration.)

## Appendix A — Canonical config-key reference (the new single source, per spec)

Legend: ⚙ = `internal` (engine-consumed, not in the form); † = renamed/re-homed by migration.

| Node type | Config fields (canonical) | Input fields | Notes |
|---|---|---|---|
| CONDITION | `data`†(array of key/operations/value), `expression`⚙ | — | `requires_one_of(data, expression)`; `data` re-homed from input_binding |
| SWITCH | `data`†(…+branch), `expression`⚙, `cases`⚙, `default`⚙ | — | `cases`/`default` pending liveness check (§11.3) |
| LOOP | `loop_type`, `collection`†, `condition`, `mode`, `max_iterations`, `parallelism`, `counter_variable` | — | `items_expression` alias dies |
| PARALLEL | `branches`⚙ | — | dead `max_concurrency`/`fail_strategy` removed |
| MERGE | `merge_strategy`†(all\|any\|n_of_m), `wait_count`†, `timeout_seconds`, `on_branch_failure`, `inputs`⚙, `defaults`⚙ | — | `wait_strategy`/`n_required` die |
| TRIGGER | `trigger_type`, `schedule_cron`, `event_topic`, `event_filter`†, `webhook_path`† | — | last two re-homed from input_binding |
| WORKFLOW | `workflow_id`, `input_mapping`†(dict, store_as), `wait_for_completion`, `timeout_seconds`, `timeout_ms`⚙, `max_depth`⚙, `on_failure`⚙, `output_binding`⚙ | — | `input_binding`→`input_mapping` |
| COMPUTE | `expression`, `result_key` | — | |
| SET_VARIABLE | `variables` (array of name/type/item_type/value_expression) | — | |
| INPUT | `instruction`, `input_schema`, `input_types`⚙, `timeout_minutes`, `file_id`, `dynamic_table`, `runThrough` | free-form bindings | pending; schema chain §6 |
| HUMAN_ACTION | `instruction`, `input_schema`, `confirm_label`, `reject_label`, `input_types`⚙ | free-form bindings | pending |
| DATA_EDIT | `file_id`†, `artifact_key`, `instruction`, `data_mode`, `query`†, `actions`†, `settings`†, `readonly`†, `custom_data_update`†, `columns`⚙, `input_types`⚙ | free-form bindings | tab-form fields get declared homes; `columns` declared for the first time |
| FILE_UPLOAD | `instruction`, `artifact_key`, `use_agent`† | free-form bindings | `use_agent` home decided at spec-writing time |
| OUTPUT | `instruction`, `output_schema` | free-form bindings | |
| APPROVAL_FLOW | `change_request_id` (expression) | — | |
| TASK (base) | `input_types`⚙ | from worker/definition schema (runtime spec, §7) | |

This table is generated from the specs once they exist (`Appendix generated by
tools/dump_node_specs.py`) so the document cannot itself drift.
