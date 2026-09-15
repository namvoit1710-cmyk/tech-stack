# NodeSpec Single-Source — Implementation Task Breakdown

**Companion to:** `node-spec-single-source.md` (the design)
**Date:** 2026-06-12
**Structure:** 6 phases, 24 tasks. Each phase is independently testable and mergeable;
the data-level clean break happens once, in Phase 6. Tasks list the files they create
(C), modify (M), or delete (D), the technical implementation, and an exit gate.

Dependency graph (phases):

```
P1 (spec core + renderer + golden tests)
 ├──> P2 (split + validation)
 │     └──> P3 (typed handler configs)
 ├──> P4 (definition_snapshot)          [independent of P2/P3, after P1]
 └──> P5 (catalog convergence + deletions)   [needs P1; deletions need P2+P3]
P6 (migration script + deploy)          [needs P2..P5 merged]
```

---

## Phase 1 — NodeSpec core, static specs, form renderer (no behavior change)

Everything in this phase is additive. Nothing existing is modified except tests.
Exit gate for the phase: golden-file tests prove the renderer reproduces today's
`SCHEMAS` output structurally for every node type, with the intentional diffs
(MERGE/WORKFLOW/PARALLEL) captured in a reviewed diff file.

### T1.1 — Capture golden fixtures of today's SCHEMAS output

- **C** `tests/fixtures/autoform_golden/<NODE_TYPE>.json` (15 files)
- **C** `tests/unit/test_autoform_golden.py`
- **Implementation:** one-off generator (a pytest helper or small script) that
  imports the *current* `SCHEMAS` dict from
  `app/layer2_application/features/list_node_palette/use_cases/control_flow_schemas.py`
  and dumps `{"input_schema": ..., "output_schema": ...}` per node type with
  `json.dumps(..., indent=2, sort_keys=True)`. Commit the fixtures BEFORE any
  renderer work so the baseline is the pre-redesign truth.
- **Test:** golden test initially asserts `SCHEMAS[t] == load_fixture(t)` (guards
  the baseline itself against accidental edits while later tasks land).
- **Exit gate:** fixtures committed; test green against unmodified code.

### T1.2 — `node_spec` package skeleton: model + registry

- **C** `app/layer1_domain/node_spec/__init__.py` (public surface: `NodeSpec`,
  `ConfigField`, `InputField`, `OutputField`, `Choice`, `VisibleWhen`,
  `FieldType`, `Widget`, `get_spec`, `BUILTIN_SPECS`, `parse_config`)
- **C** `app/layer1_domain/node_spec/model.py`
- **C** `app/layer1_domain/node_spec/registry.py`
- **Implementation:** frozen dataclasses exactly as design §3.1. Reuse the
  existing `NodeType`, `NodeClass`, `Port` value objects (no new port model).
  `registry.py`:
  ```python
  BUILTIN_SPECS: dict[NodeType, NodeSpec] = {}   # populated by specs/ modules

  def register(spec: NodeSpec) -> NodeSpec:
      BUILTIN_SPECS[spec.node_type] = spec
      return spec

  def get_spec(node_type: NodeType) -> NodeSpec:
      return BUILTIN_SPECS.get(node_type) or _MINIMAL_TASK_SPEC
  ```
  Add `__post_init__`-style invariant checks on `NodeSpec` construction
  (duplicate field keys, `visible_when.field` references an existing sibling,
  `requires_one_of` keys exist, choices values unique) so a malformed spec
  fails at import time, not at request time.
- **Test:** `tests/unit/node_spec/test_model.py` — invariant violations raise;
  frozen dataclasses hashable; `get_spec` falls back to the TASK minimal spec.
- **Exit gate:** unit tests green; package imports with zero specs registered.

### T1.3 — Author all 16 static specs + typed configs

- **C** `app/layer1_domain/node_spec/specs/{condition,switch,loop,parallel,merge,trigger,workflow_node,compute,input,human_action,data_edit,file_upload,output,approval_flow,set_variable,task}.py`
- **C** `app/layer1_domain/node_spec/specs/__init__.py` (imports all modules so
  registration runs)
- **Implementation:** transcribe from the canonical-key reference
  (design Appendix A). Source of truth for each field's existence is the
  *handler read*, not the old form. Per file: `XXX_SPEC = register(NodeSpec(...))`
  plus the frozen `XxxConfig` dataclass with
  `from_node(cls, node) -> XxxConfig: return parse_config(XXX_SPEC, cls, node)`.
  Key decisions already made in the design — encode them verbatim:
  - MERGE: canonical vocabulary `all|any|n_of_m`; `inputs`/`defaults` internal.
  - TRIGGER: all five fields config; conditional `required` via `visible_when`.
  - CONDITION/SWITCH: `data` is a config ARRAY field; `expression` internal;
    `requires_one_of=(("data","expression"),)`. Define
    `COMPARISON_OPERATOR_CHOICES` once in
    `app/layer1_domain/node_spec/operators.py`, generated from the canonical
    operator list used by `app/layer2_application/services/value_comparator.py`
    (move the list to layer 1 if it currently lives in layer 2; the comparator
    imports it — single definition).
  - WORKFLOW: `input_mapping` (`store_as="dict"`,
    `item_fields=(param_name, param_value)`); `timeout_ms`, `max_depth`,
    `on_failure`, `output_binding` internal.
  - LOOP: `collection` only (no `items_expression`).
  - PARALLEL: `branches` internal only — no form fields.
  - DATA_EDIT: declare `columns` internal; give the tab-form fields (`file_id`,
    `query`, `actions`, `settings`, `readonly`, `custom_data_update`) explicit
    config homes; keep the TAB widget structure via `Widget.TAB` + nested
    `item_fields`.
  - SWITCH `cases`/`default`: declare internal, marked with a
    `# liveness check pending — design §11.3` comment (resolution in T6.2).
- **C** `tests/unit/node_spec/test_spec_alignment.py` — the drift guard:
  parametrized over `BUILTIN_SPECS`, asserts
  `set(f.name for f in dataclasses.fields(XxxConfig)) == {f.key for f in spec.config_fields}`
  (discover the config class by convention: module attribute ending in `Config`).
- **Exit gate:** all 16 specs import; alignment test green.

### T1.4 — `parse_config` (typed access parser)

- **M** `app/layer1_domain/node_spec/model.py` (add `parse_config`)
- **Implementation:**
  ```python
  def parse_config(spec: NodeSpec, cls: type[T], node: WorkflowNode) -> T:
      raw = node.config or {}
      kwargs = {}
      for f in spec.config_fields:
          value = raw.get(f.key, f.default)
          kwargs[f.key] = _coerce(f, value)   # same rules as parameter_mapper
      return cls(**{k: v for k, v in kwargs.items() if v is not None} | defaults_handling)
  ```
  Coercion table (shared `_coerce` with `parameter_mapper.py`): NUMBER → int-if-
  integral-else-float via the existing `_coerce_config_value` logic (port it,
  don't import from `workflow.py` — it gets deleted in P5); BOOLEAN → the
  existing truthy-string table; ARRAY/OBJECT/JSON pass through (JSON: parse if
  str). Values that are `expression=True` templates (`"{{" in value`) bypass
  coercion (resolved at runtime).
- **Test:** `tests/unit/node_spec/test_parse_config.py` — defaults applied,
  coercion, template bypass, unknown keys in `node.config` ignored.
- **Exit gate:** green.

### T1.5 — Form renderer with golden parity

- **C** `app/layer1_domain/node_spec/form_renderer.py`
- **M** `tests/unit/test_autoform_golden.py`
- **Implementation:** `render_input_schema(spec)` / `render_output_schema(spec)`
  per design §4.1 mapping rules, built on `app/layer1_domain/auto_form_mapper`
  helpers. The tricky parts, in order of effort:
  1. `visible_when` → inverted `ConditionConfig(field_key, NOT_EQUALS, value)`
     (tuple `equals` → multiple hidden-when conditions or the mapper's IN/NOT_IN
     operator if it exists — check `auto_form_mapper/enums.py`).
  2. Wrapper conventions: ARRAY fields need per-spec wrapper hints
     (`FRAGMENT`+`FORM_ITEM` for CONDITION/SWITCH rows, `TABLE_ROW`+`TABLE_CELL`
     for WORKFLOW/HUMAN_ACTION) — add an optional
     `ConfigField.wrapper_hint: str | None` rather than guessing.
  3. DATA_EDIT's TAB nesting and OUTPUT's positional `"0"` template-row quirk:
     reproduce via `Widget.TAB` rendering and an `array_template_row=True` hint.
     If a quirk costs more to express in the model than it's worth, prefer
     changing the golden (FE confirms structural tolerance) over polluting the
     model — record each such decision in the diff file (T1.6).
  - Output side: `output_fields` → the `_readonly_*` builders (move those four
    helpers from `control_flow_schemas.py` into the renderer now; the old file
    keeps its own copies until deleted in P5).
- **Test:** golden test gains mode 2: `render_input_schema(get_spec(t))` vs
  fixture, normalized by `sort_keys` JSON round-trip. Run per node type.
- **Exit gate:** byte-parity (post-normalization) for the 12 unchanged node
  types. MERGE/WORKFLOW/PARALLEL handled in T1.6.

### T1.6 — Intentional golden diffs (the FE-coordination artifact)

- **M** `tests/fixtures/autoform_golden/{MERGE,WORKFLOW,PARALLEL}.json`
- **C** `docs/design/node-spec-fe-changes.md`
- **Implementation:** regenerate the three goldens from the new specs. Write the
  FE-changes doc listing exactly: MERGE select option ids `wait_all→all`,
  `wait_any→any`, `wait_n→n_of_m` (labels unchanged); WORKFLOW field key
  `input_binding→input_mapping`; PARALLEL `max_concurrency`/`fail_strategy`
  fields removed. This doc is what gets handed to the FE team; the FE ships in
  lockstep (design §10.2).
- **Exit gate:** golden tests green for all 15; FE doc reviewed.

---

## Phase 2 — Spec-driven split + save-time validation

### T2.1 — `parameter_mapper.py` and `WorkflowNode` cutover

- **C** `app/layer1_domain/node_spec/parameter_mapper.py`
- **M** `app/layer1_domain/entities/workflow.py`
- **Implementation:** `split_parameters(spec, parameters)` / `merge_parameters`
  per design §4.2, including the `store_as="dict"` fold for WORKFLOW
  `input_mapping` (array of `{param_name, param_value}` rows → dict; tolerate
  dict passthrough for API-authored nodes). `WorkflowNode.__post_init__` line
  ~172 changes to:
  ```python
  if self.parameters and not self.config and not self.input_binding:
      from app.layer1_domain.node_spec import get_spec
      from app.layer1_domain.node_spec.parameter_mapper import split_parameters
      self.config, self.input_binding = split_parameters(
          get_spec(self.node_type), self.parameters)
  ```
  (intra-layer import; keep it function-local if a cycle appears —
  `workflow.py` ← `node_spec` must not import `workflow.py` at module level;
  `parse_config` takes the node duck-typed to avoid the cycle).
  **Do NOT delete** `_CONFIG_KEYS`/`_CONFIG_TYPE_MAP`/old `split_parameters`
  yet — they go in P5 after nothing references them. Old functions get a
  module-level comment "superseded by node_spec.parameter_mapper — deleted in
  P5".
- **Test:** `tests/unit/node_spec/test_parameter_mapper.py` — per node type,
  representative `parameters` dicts split to the same `(config, input_binding)`
  as the old path EXCEPT the intentional re-homings (TRIGGER webhook/event
  fields → config; CONDITION/SWITCH `data` → config; WORKFLOW rename+fold).
  Write the exception list as explicit test cases — it doubles as the
  normalization spec for T6.1.
- **Exit gate:** full unit suite green (tests asserting the OLD split for the
  re-homed keys are updated in this task, not skipped).

### T2.2 — `validation.py` + expression syntax checker

- **C** `app/layer1_domain/node_spec/validation.py`
- **M** `app/layer2_application/services/variable_resolver.py` (extract)
- **Implementation:** `SpecViolation` + `validate_node(spec, node)` per design
  §4.3 (6 codes; visibility-aware required; enum skip for templates;
  `item_fields` recursion). Extract a pure
  `validate_expression_syntax(text) -> str | None` from `VariableResolver`'s
  tokenizer: balanced `{{ }}`, non-empty reference body, known reference root
  (`$nodeId|$input|$variables|$variable|$.`). Place it in layer 1
  (`app/layer1_domain/node_spec/expression_syntax.py`) and have
  `variable_resolver.py` import it so there is one tokenizer truth.
- **Test:** `tests/unit/node_spec/test_validation.py` — one test per violation
  code per representative spec; the TRIGGER conditional-required matrix
  (schedule_cron required iff trigger_type==schedule); CONDITION exclusive
  group; template-skips-enum.
- **Exit gate:** green.

### T2.3 — Wire validation into save use cases (log-only first)

- **C** `app/layer2_application/services/node_config_validator.py`
- **M** `app/layer2_application/features/create_workflow/use_cases/create_workflow_use_case.py`
- **M** `app/layer2_application/features/update_workflow/use_cases/update_workflow_use_case.py`
- **M** `app/layer2_application/errors.py` (add `ErrorCode.NODE_CONFIG_INVALID`)
- **Implementation:** `validate_workflow_nodes(workflow, spec_resolver)` —
  resolves the spec per node (builtin path only until P5's resolver exists; for
  TASK/worker/business nodes validate only the minimal TASK config), collects
  all violations. Insert after node construction, before
  `_enrich_nodes_with_definitions` (create) / before save (update). Behavior
  behind env flag `NODE_CONFIG_VALIDATION` = `off|warn|enforce`, default
  `warn`: log violations with workflow_id/node_id, return success. `enforce`
  returns the 400 shape (design §8 payload). The flag flips to `enforce` as
  part of the P6 deploy, then the flag is removed (clean break — the flag is a
  rollout tool inside ONE release, not a living toggle).
- **Test:** `tests/unit/test_create_workflow_validation.py` +
  integration test asserting the 400 `error_details.violations[]` shape in
  enforce mode (reuse the existing edge-reference-error test pattern).
- **Exit gate:** suite green in `warn`; one staging save-traffic pass with zero
  unexpected warnings on freshly-authored workflows.

---

## Phase 3 — Typed handler configs (delete fallbacks)

Mechanical conversions, one task per handler cluster so reviews stay small.
Pattern per handler: replace `node.config.get(...)` chains with
`cfg = XxxConfig.from_node(node)`; delete legacy-key fallbacks, vocabulary
maps, and inline type checks; update the handler's tests to canonical keys and
ADD a test asserting the legacy key is now ignored.

### T3.1 — Routing handlers: CONDITION, SWITCH

- **M** `node_handlers/condition_handler.py`, `node_handlers/switch_handler.py`
- **Implementation:** `_evaluate` reads `cfg.data` (config) — the
  `input_binding["data"]` read (`condition_handler.py:145`,
  `switch_handler.py:317`) is deleted; structured-vs-expression choice becomes
  `if cfg.data: ... else: cfg.expression` (exclusivity guaranteed at save).
  Resolution of rule values keeps using the existing resolver calls unchanged.
- **Caveat:** until P6 normalizes stored data, old workflows have `data` in
  `input_binding`. These handler changes therefore merge to the release branch
  but the release ships only with P6's migration (single-release clean break
  per design §9). Same caveat applies to T3.2–T3.5.

### T3.2 — MERGE, PARALLEL

- **M** `node_handlers/merge_handler.py` (delete `_STRATEGY_MAP` at :51-54,
  both `or`-chains at :42-62), `node_handlers/parallel_handler.py`
  (`cfg.branches`).

### T3.3 — WORKFLOW, LOOP

- **M** `node_handlers/workflow_handler.py` — `cfg.input_mapping` (dict
  guaranteed by `store_as` fold; delete the list→dict conversion at :59-64),
  `cfg.max_depth`/`cfg.timeout_*`/`cfg.on_failure`/`cfg.output_binding`; the
  manual `max_depth` type-sanitizing at :40-44 collapses into `parse_config`
  coercion.
- **M** `services/loop_driver/event_driven_loop_driver.py:800-849` —
  `LoopConfig.from_node(node)`; delete the `collection or items_expression`
  fallback at :818.

### T3.4 — TRIGGER, COMPUTE, SET_VARIABLE, APPROVAL_FLOW, OUTPUT

- **M** `node_handlers/{trigger,compute,set_variable,approval_flow,output}_handler.py`
- Straight reads → typed configs. TRIGGER also: confirm the CP scheduler
  (crontab tick loop) and webhook routing read `schedule_cron` from
  `node.config` (they do today — the spec keeps that key in config, no
  behavior change; this is open question §11.4's verification, close it here).

### T3.5 — PENDING handlers: INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD

- **M** `node_handlers/{input,human_action,data_edit,file_upload}_handler.py`,
  `node_handlers/pending_base.py`
- **Implementation:** hook structure untouched; swap reads
  (`pending_base.py:175,220,341` — `input_types`, `instruction`, `runThrough`;
  `input_handler.py:106-303`; `data_edit_handler.py:256` `columns` now a
  declared field). DATA_EDIT's newly-homed tab fields (`file_id`, `query`,
  `actions`, `settings`, `readonly`, `custom_data_update`) read via
  `DataEditConfig`.
- **Exit gate (phase):** full suite green; `grep -rn "wait_strategy\|n_required\|items_expression\|config.get(\"input_binding\"" app/` returns zero hits outside the migration tool.

---

## Phase 4 — `definition_snapshot` (snapshots out of config)

### T4.1 — Entity field + serialization

- **M** `app/layer1_domain/entities/workflow.py` — add
  `definition_snapshot: dict[str, Any] | None = None` to `WorkflowNode`;
  carry it through `_node_to_snapshot`/`from_snapshot_dict`; `merge_parameters`
  keeps the `_`-prefix filter but gains
  `assert not any(k.startswith("_") for k in config)` under `__debug__`
  (removed in P6 cleanup).
- **M** persistence: verify the HANA workflow repo serializes nodes via the
  entity snapshot helpers (it does — nodes live inside the workflow JSON
  document; no column migration needed). If any repo hand-builds node dicts,
  add the field there.

### T4.2 — Writer + reader migration

- **M** writers: `create_workflow_use_case.py` enrichment
  (`_enrich_nodes_with_definitions`), `update_workflow_use_case.py` equivalent —
  write the merged snapshot dict (`_node_definition` ∪ `_node_internals`
  shapes) to `node.definition_snapshot`; stop writing the two config keys.
- **M** readers (6): `orchestration_engine.py:954,962`,
  `task_handler.py:137,166`, `schema_providers.py:117-125`,
  `complete_task_use_case.py:161`, `get_node_execution_use_case.py:112`,
  `get_variable_suggestions_use_case.py:530` — read
  `node.definition_snapshot` only (no config fallback; stored-data migration
  in P6 moves old runs' data).
- **Test:** existing node-definition enrichment tests updated; add one test
  per reader site asserting `_node_definition` in config is now IGNORED.

### T4.3 — PENDING schema chain 4→3

- **M** `node_handlers/schema_providers.py` — chain becomes: runtime
  (`task.output_metadata.input_schema`) → `node.definition_snapshot["input_schema"]`
  → `node.config["input_schema"]`. Delete the JSON-string-parsing branch
  (:128-135). Pre-req: T6.1's migration report confirms no string schemas
  remain (or migration parses them).
- **M** `tests/unit/test_schema_providers.py` (string-schema tests become
  "string schema → empty + warn log").
- **Exit gate (phase):** suite green; `grep -rn "_node_definition\|_node_internals" app/` → only the migration tool.

---

## Phase 5 — Catalog convergence + deletions

### T5.1 — `NodeSpecResolver` + worker/definition spec builders

- **C** `app/layer2_application/services/node_spec_resolver.py`
- **Implementation:** per design §7 — `resolve(node)`: builtin → `get_spec`;
  `node_definition_id` → `spec_from_node_definition(defn)` (base-type spec
  overlaid with `defn.input_schema` as InputFields + ports/icon/color);
  `worker_type` → `spec_from_worker(worker_dict)`. Plus
  `spec_from_autoform(fields: list[dict]) -> tuple[InputField, ...]` — the
  inverse of the renderer for the *simple* subset (key, type, label, required,
  placeholder); unmappable complex fields pass through as opaque InputFields
  (`type=OBJECT`) — lenient by default, strict under the existing
  `strict_schema_validation` flag.
- **M** `node_config_validator.py` — accept the resolver; worker/business
  config keys now validate against the resolved spec; unknown `input_binding`
  keys → warn-level violations (severity field added to `SpecViolation`).
- **Test:** `tests/unit/test_node_spec_resolver.py` (mirror the fixtures of
  `test_worker_node_provider.py` / `test_business_node_provider.py`).

### T5.2 — Converge the three catalog providers + palette

- **M** `providers/builtin_node_provider.py` — serve
  `render_*_schema(get_spec(t))` instead of importing `SCHEMAS`.
- **M** `providers/worker_node_provider.py`, `providers/business_node_provider.py`
  — route through `spec_from_worker`/`spec_from_node_definition` + renderer
  (response DTOs unchanged; the existing `FormField.model_validate` stays as
  the structural guard).
- **M** `features/list_node_palette/use_cases/list_node_palette_use_case.py` —
  `BUILTIN_NODES` builds from specs (name/description/category/ports/icon all
  come from the spec; drop the duplicated `_node()` literals).
- **Test:** `test_node_palette_v2.py`, `test_builtin_node_provider.py` etc.
  keep their response-shape assertions — they now run against spec-rendered
  output; golden tests already pin the field dicts.

### T5.3 — Delete the superseded catalogs

- **D** `features/list_node_palette/use_cases/control_flow_schemas.py`
- **D** `app/layer1_domain/value_objects/node_type_registry.py` — first migrate
  its remaining importers (`workflow.py`? `list_node_palette_use_case.py:12`,
  validation of min/max out ports wherever edges are validated — grep
  `get_descriptor|get_all_descriptors`) to `get_spec`.
- **M** `app/layer1_domain/entities/workflow.py` — delete `_CONFIG_KEYS`,
  `_CONFIG_TYPE_MAP`, old `split_parameters`, `_coerce_config_value`.
- **D** `tests/unit/test_control_flow_schemas.py` (superseded by golden +
  alignment tests).
- **Exit gate (phase):** `grep -rn "_CONFIG_KEYS\|control_flow_schemas\|node_type_registry" app/ tests/` → zero (excluding migration tool + this doc).

### T5.4 — Test fixture builder

- **C** `tests/helpers/node_builders.py` —
  `make_node(node_type, *, name=..., **params) -> WorkflowNode` constructing via
  `parameters=params` so every test node goes through the real split; plus
  `make_workflow(nodes, edges)`. Convert the highest-churn test modules
  (handler tests touched in P3) to use it; leave the long tail for follow-ups.

---

## Phase 6 — Stored-data normalization + deploy

### T6.1 — `tools/normalize_node_config.py`

- **C** `tools/normalize_node_config.py`
- **Implementation:** idempotent, two-pass, against the configured repo
  backend (HANA or memory):
  1. **Report pass (`--dry-run`, default):** for every workflow (and every
     workflow-run snapshot — the frozen `workflow` document inside runs):
     compute the transform set per the design §9 table, print a per-workflow
     unified diff of `(config, input_binding)` per node, and a summary:
     counts per transform, residual `SpecViolation`s (via `validate_node`),
     JSON-string `input_schema` occurrences (gates T4.3), `wait_strategy`
     occurrences in run snapshots, SWITCH nodes with `cases`/`default` set
     (answers design §11.3).
  2. **Apply pass (`--apply`):** per workflow: load → transform nodes →
     re-run `split_parameters(spec, merge_parameters(config, binding))` to
     re-home strays → save via the repo (CAS-aware: retry on version
     conflict). Transforms implemented as small pure functions, one per design
     §9 table row, each individually unit-tested with before/after dicts.
  Idempotency: every transform is a no-op on already-canonical data; running
  twice produces an empty diff.
- **Test:** `tests/unit/tools/test_normalize_node_config.py` — table-row
  transforms; idempotency (`apply(apply(x)) == apply(x)`); a synthetic
  workflow exercising every legacy shape at once.

### T6.2 — Resolve the SWITCH `cases`/`default` open question

- Driven by T6.1's report: if zero live occurrences → drop the keys from the
  SWITCH spec and delete the handler branch (`switch_handler.py:187-188`);
  if live → keep as `internal` (already declared in T1.3) and document the
  authoring path. Either way, close design §11.3 with a doc edit.

### T6.3 — Migration entry + deploy choreography

- **C** `db/migrations/042_normalize_node_config.md` (runbook entry — the
  normalization is application-level, not SQL; the numbered entry records
  ordering relative to schema migrations)
- **Deploy order (one release):** ① merge P2–P5 branch; ② run
  `normalize_node_config.py --dry-run` against prod data, review report;
  ③ deploy new code with `NODE_CONFIG_VALIDATION=warn`; ④ run `--apply`;
  ⑤ flip `NODE_CONFIG_VALIDATION=enforce`; ⑥ follow-up PR removes the flag and
  the `__debug__` assertion from T4.1. FE deploys its lockstep change
  (T1.6 doc) at step ③.
- **Exit gate:** post-apply `--dry-run` rerun shows empty diff; live smoke:
  author + run one workflow per re-homed node type (MERGE n_of_m, TRIGGER
  schedule, CONDITION structured, WORKFLOW sub-workflow with input_mapping).

---

## Cross-cutting notes

- **Effort shape:** P1 is the bulk of the new code (renderer parity is the
  long pole — budget the DATA_EDIT/OUTPUT quirks); P3 is wide but mechanical;
  P6 is small code but the only risky deploy step.
- **Branch strategy:** P1 can merge to dev immediately (additive). P2–P5 ride
  one feature branch (they change behavior for re-homed keys and must ship
  with the migration). P6 tool can merge early; the *apply* happens at deploy.
- **Suite baseline:** record the failing-test baseline before P2 (the repo
  carries known reds); every phase's exit gate is "no new reds", not "all
  green".
- **What NOT to build:** no compat shims, no dual-read, no deprecation
  warnings on old keys (they 400 after enforce), no per-tenant rollout — all
  per the clean-break decision.
