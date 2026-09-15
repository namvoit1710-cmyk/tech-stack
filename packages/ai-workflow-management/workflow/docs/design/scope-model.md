# Scope Model — Variable Namespaces, Lifetimes, and Validation Timing

**Status:** Reference documentation — 2026-04-12

**Audience:** workflow authors writing expressions like `{{$input.x}}` / `{{$node_a.output.y}}`; engine contributors adding new scopes or handlers; validators.

---

## 1. What a "scope" is in this system

In the `workflow-control-plane-service` engine, a **scope** is one of the top-level namespaces that a user expression can reference through `{{ $... }}` / `$.node_id.field` syntax. At runtime, every expression is resolved by walking the configured `VariableResolver` strategy chain against a single dict: `run.context.variables`. Everything the resolver can see lives under one of the keys below.

The word *"scope"* is overloaded in the codebase. Two distinct meanings co-exist and should not be confused:

| Term | What it actually refers to |
|---|---|
| **Variable scope** (this doc, §3) | A top-level key in `run.context.variables` — `$input`, `$trigger`, `$loop`, `$artifacts`, `$<node_id>`, etc. The user-visible namespace. |
| **`LoopScope`** (Phase 4c.4 value object) | One frame on a task's `scope_stack: list[LoopScope]` — per-iteration bookkeeping for nested loops. **Not directly referenceable from an expression.** |

§5 of this doc ties the two together: a `LoopScope` drives *which* `$loop` and *which* `invocation_id` a body task sees, but the body task itself reads `{{$loop.index}}` just like a flat loop does.

There is also a higher-level grouping the code doesn't name but users should understand — a scope's **lifetime** answers "when does this namespace exist". That's the frame this doc uses throughout:

| Lifetime | Owns | Cleared when |
|---|---|---|
| **Workflow lifetime** | Workflow definition (static, cached) | Workflow deleted / updated |
| **Run lifetime** | `run.context.variables`, `run.context.loop_state`, `run.context.merge_quorum_buffers` | Run reaches terminal state (retained until then, even across pauses/resumes) |
| **Task lifetime** | `Task.loop_context`, `Task.iteration_index`, `Task.scope_stack` | Task completes (the task entity persists in the repo but is no longer active) |
| **Iteration lifetime** | Entries under `run.context.loop_state[loop_id]` for the *current* iteration | Next iteration starts (values overwrite) |

These four lifetimes map cleanly onto what users commonly call **workflow scope**, **run scope**, **node scope**, and **loop scope** — but that terminology doesn't appear in the code. Pick whichever framing you prefer; this doc uses both interchangeably with explicit cross-references.

---

## 2. Lifetime taxonomy — the four "big" scopes

### 2.1 Workflow scope (workflow lifetime)

**What it is:** read-only metadata about the workflow definition. Immutable within a run.

**Storage:** seeded into `run.context.variables["workflow"]` at run start by `StartRunUseCase._start_run` (`start_run_use_case.py:126-131`).

**Shape:**
```python
{
    "id": workflow.id,
    "name": workflow.name,
    "version": workflow.version,
    "description": workflow.description,
}
```

**Expression syntax:** `{{$workflow.id}}`, `{{$workflow.name}}`, etc.

**When to use:** a node handler needs to reference which workflow it belongs to (audit logs, conditional logic keyed on workflow id, child-workflow dispatch).

### 2.2 Run scope (run lifetime)

**What it is:** everything the engine accumulates over the lifetime of a single run. Has four sub-compartments, three visible to expressions and one private.

**Storage:** `run.context` — an entity field that stays in memory (and in the run repo) from `orchestrate_start` through the terminal state.

| Sub-compartment | Visible to expressions | Purpose |
|---|---|---|
| `context.variables` | Yes — every user expression walks it | Node outputs, loop iteration vars, artifacts, seeded metadata |
| `context.loop_state` | No (internal handler state) | Per-loop-node iteration bookkeeping (items, current_index, invocation_id, etc.) |
| `context.merge_quorum_buffers` | No (Phase 4c.4.4 internal) | Scope-aware merge quorum buffers keyed by `(node_id, invocation_id, iter)` |
| `context.completed_nodes` / `skipped_nodes` | No (queried indirectly by DAG successor logic) | Which DAG nodes have reached a terminal state |

At run start (see `start_run_use_case.py:123-152`), `context.variables` is seeded with four root keys:

```python
context.variables = {
    "input":     <file_ref or dict from req.input_data>,  # $input
    "workflow":  {"id": ..., "name": ..., "version": ...}, # $workflow
    "artifacts": {},                                       # $artifacts (empty!)
    "run":       {"id": ..., "tenant_id": ..., ...},      # $run
}
```

Note: **`artifacts` starts empty**. It is populated *dynamically* as upstream nodes complete (see §4).

### 2.3 Node scope (per-node output, run lifetime)

**What it is:** the output of each node that has completed so far. New entries are added over time; existing entries can be overwritten (e.g., by loop bodies that re-run the same node).

**Storage:** `run.context.variables[<node_id>]` — written by `SuccessorProcessor` after a handler returns `NodeHandlerResult.state_updates`.

**Shape convention:** most handlers write `{"output": <payload>, "metadata": {"type": ..., ...}}`. A few handlers deviate:
- `ComputeNodeHandler` can additionally write a top-level key via `result_key` config → `variables["<result_key>"] = value` — an arbitrary user-chosen variable name.
- `TriggerNodeHandler` writes both `variables["<trigger_node_id>"]` AND `variables["trigger"]` — so `$trigger` is the documented alias for "whichever TRIGGER node fired".
- `LoopNodeHandler` writes `variables["loop"]` on each iteration — see §2.4.

**Expression syntax:** `{{$node_id.output.field}}` or `{{$node_name.output.field}}` (human-readable aliases are added by the `SuccessorProcessor` — see the `by_name` mapping pass).

**Dynamic keys inside node outputs:** many handlers write keys whose names are *not* knowable at design time (see §4).

### 2.4 Loop scope (iteration lifetime, nested via Phase 4c.4 `scope_stack`)

**Two data structures share the name "loop scope":**

#### 2.4a `run.context.variables["loop"]` — the user-visible `$loop` dict

Rewritten at the start of each iteration by `LoopNodeHandler._initialize_loop` / `_handle_iteration_complete`:

```python
{
    "index": int,      # current iteration, 0-based
    "item":  Any,      # current collection element
    "total": int,      # total iterations (known)
    "first": bool,     # iter == 0
    "last":  bool,     # iter == total-1
    "depth": int,      # nesting depth (hard-coded 0 today in legacy path)
}
```

**Expression syntax:** `{{$loop.index}}`, `{{$loop.item}}`, `{{$loop.first}}`.

**Critical limitation in legacy (non-event-driven) mode:** `$loop` is a single dict. Nested loops overwrite each other — inner iter 0 clobbers outer iter 2, and there is no way to reference the outer loop's iteration from inside the inner body. This is the bug `EVENT_DRIVEN_LOOP=true` + Phase 4c.4.3 addresses (via `scope_stack` on each body task). The user-visible `$loop` dict still reflects the *innermost* iteration — the scope stack is consumed by the merge handler (§6), not by the variable resolver.

#### 2.4b `Task.scope_stack: list[LoopScope]` — task-carried nested-loop context

Each body task inside an event-driven loop carries a full stack of `LoopScope` frames (one per enclosing loop, outermost first). A `LoopScope` is:

```python
@dataclass(frozen=True)
class LoopScope:
    loop_node_id: str    # which LOOP node emitted this frame
    invocation_id: str   # uuid5(run_id, loop_node_id, enclosing) — stable
                         # across iterations of the same invocation, different
                         # for each outer iteration of a nested loop
    iter: int            # 0-based iteration index within this invocation
    total: int           # total iterations (or -1 for condition loops)
```

The `scope_stack` is **task-lifetime**, not expression-visible. It is consumed by:
- `MergeNodeHandler` to key quorum buffers per-iteration (Phase 4c.4.4).
- Future `LoopExitRequested` / break handlers (Phase 5+) to identify which invocation to terminate.

See `docs/design/loop-event-driven-spike.md` §3.2 for the full design and `docs/design/event-driven-execution-plan.md` §Phase 4c.4/4c.4.2/4c.4.3/4c.4.4 for the migration history.

---

## 3. The complete scope table — every `$`-prefixed namespace

Every top-level key in `run.context.variables` is addressable as `{{ $<key> }}` by the variable resolver. This table enumerates all of them.

| Scope | Lifetime | Populated by | Shape | Dynamic keys? | Notes |
|---|---|---|---|---|---|
| **`$input`** | Run | `StartRunUseCase:124-125` | file_ref OR plain dict | **Yes** — arbitrary user-supplied fields | Resolved via `FileBackedResolverStrategy` when stored as a CSV file_ref |
| **`$workflow`** | Run | `StartRunUseCase:126-131` | `{id, name, version, description}` | No — static | Immutable for the run |
| **`$run`** | Run | `StartRunUseCase:147-152` | `{id, tenant_id, correlation_id, started_at}` | No — static | Immutable for the run |
| **`$artifacts`** | Run (grows) | `register_artifacts()` called after each completed task | `{artifact_name: file_id, ...}` | **Yes** — every artifact name is runtime-determined | See §4 |
| **`$trigger`** | Run (set once) | `TriggerNodeHandler.handle` writes both `$<trigger_node_id>` and `$trigger` | `{type, body}` where `body = run.input_data` | No — shape is fixed | Alias for the TRIGGER node's output |
| **`$loop`** | Iteration (rewritten each iter) | `LoopNodeHandler` | `{index, item, total, first, last, depth}` | No — fixed keys | Only valid inside a LOOP body |
| **`$<node_id>`** (e.g. `$my_compute_node`) | Run (grows) | `SuccessorProcessor` copies `state_updates[node_id]` into `variables[node_id]` after each handler returns | `{output: ..., metadata: ...}` or arbitrary shape | **Yes** — `output` payload shape is handler-specific | One entry per completed node |
| **`$<node_name>`** (e.g. `$"My Compute Step"`) | Run (grows) | Same as `$<node_id>` via `by_name` alias pass | Same as `$<node_id>` | **Yes** | Human-readable alias; the engine maintains both keys pointing at the same payload |
| **`$metadata`** | Run (resolved on demand) | Not seeded — resolved by `MetadataResolverStrategy` from an ambient `metadata.extracted` dict attached to file refs | Pre-extracted scalars from CSV headers etc. | **Yes** — keys depend on uploaded file contents | Only meaningful when the source of an expression is a file-backed ref |
| **`$config`** (user-defined) | Run | **Not populated by the engine** | Intended for workflow-author-defined config | **Yes** — anything the author declares | Design-time validation does NOT reject unresolved `$config` references; they fail at task execution with `ExpressionError` if the author never wired them |
| **`$result_key`** (arbitrary top-level variable) | Run (grows) | `ComputeNodeHandler` if `result_key` config is set | The computed scalar/dict directly (no wrapping) | **Yes** — name is arbitrary | Lets a COMPUTE node publish `{{$my_total}}` instead of `{{$compute_node.output.result}}` |

**Reference:** for the exact population code, grep for `state_updates[` in `app/layer1_domain/services/node_handlers/` and `context.variables[` in `start_run_use_case.py`.

---

## 4. Static vs dynamic scopes — the critical distinction

A scope is **static** if every valid expression path is known at workflow-save time, from the workflow definition alone. A scope is **dynamic** if new paths appear during run execution in response to data that only exists at runtime.

The user's original question was: "ensure dynamic scope append like design time, $artifacts scope and more due the some of node that create output key after run". This section addresses that directly.

### 4.1 Dynamic population — real examples

| Source | What it adds | When |
|---|---|---|
| **`register_artifacts()`** at `data_reference.py:83` | New keys under `$artifacts.<name>` — one per named artifact in an upstream node's output | Called after `complete_task`, `add_node_artifact`, `resume_pending_task` — i.e. AFTER the producing task is done |
| **INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD handlers** | Arbitrary keys inside `$<node_id>.output` — the shape is whatever the user submitted via the form | When the user responds to the PENDING task |
| **COMPUTE `result_key`** | A new top-level variable like `$my_sum` published directly under `variables` (not nested under the node id) | When the COMPUTE handler runs |
| **MERGE with default inputs** | `$<merge_node>.output.<predecessor_id>` — keys derived from which branches actually completed | When the merge fires |
| **LOOP body node outputs** | Each body node's output is rewritten on every iteration — design time sees one `$<body_node>.output` but its contents change per iter | Every iteration |
| **TASK (worker) results** | Arbitrary worker response shape copied into `$<task_node>.output` | When the worker POSTs `complete_task` |
| **WORKFLOW (child run) results** | Whatever the child workflow's final output looks like | When the child run completes |

**Concrete example: `$artifacts.uploaded_data`**

1. Workflow designer adds a FILE_UPLOAD node called `upload_invoice` with `artifact_key: "invoice_pdf"`.
2. At workflow save, the validator sees the node but cannot know that `$artifacts.invoice_pdf` will resolve — because `$artifacts` is empty at save time and will only be populated at runtime.
3. At run start, `context.variables["artifacts"] = {}`.
4. A user uploads a PDF through the frontend; `complete_task_use_case` stores the file and calls `register_artifacts(variables, task_output)`. This mutates `variables["artifacts"]["invoice_pdf"] = "<file-id>"`.
5. A downstream TASK node with `input_binding: {"attachment": "{{$artifacts.invoice_pdf}}"}` resolves successfully because the key now exists in `context.variables`.

If step 4 never happened (e.g. the user skipped the upload), step 5 raises `VariableResolutionError` → `ExpressionError` at task dispatch → the task is marked FAILED. **There is no design-time guarantee that `$artifacts.invoice_pdf` will be populated** — the validator cannot follow data-flow-dependent branches into the future.

### 4.2 What this means for the validator

Because of these dynamic scopes, **design-time reference validation is deliberately disabled** in `DAGValidator.validate()` (`dag_validator.py:26-41`). The `_check_references` method exists in the file but is commented out of the pipeline — forcing it on would reject every workflow that uses `$artifacts`, `$config`, `$result_key`, or an arbitrary `$input` field. The trade-off is: unresolvable references become runtime errors instead of save-time errors.

See §5 for exactly when references *do* get checked.

### 4.3 What a new scope-contributing handler must do

If you add a new node handler that introduces a new dynamic key, you must:

1. Write the key into `result.state_updates` inside `handle()` — either nested under `state_updates[node.id]` (to appear as `$<node_id>.output.<key>`) or as a top-level `state_updates[<top_level_key>]` (to appear as `${top_level_key}`).
2. If the key is a file_ref with named artifacts, ensure the completion path calls `register_artifacts(run.context.variables, node_output)` — currently `complete_task_use_case.py:190`, `add_node_artifact_use_case.py:83`, `resume_pending_task_use_case.py:400`. **Only these three use-cases call `register_artifacts` today.** A new code path that bypasses them will not populate `$artifacts`.
3. Document the dynamic key in the node's schema docs so workflow authors know what to expect.
4. Do NOT add the new key to `DAGValidator._check_references` — the validator must remain data-flow-agnostic.

---

## 5. Validation timing — when does each check happen?

This is the user's second question: "by when the scope be check? check before run the workflow or check before run task". Here is the complete answer.

There are **four** validation checkpoints. Each catches a different class of error, and none of them validates every possible runtime expression — because §4 tells us that would be impossible.

### 5.1 Checkpoint A — Workflow save (design time)

**When:** `POST /workflows` or `PUT /workflows/{id}` — inside the `workflow_use_case` feature.

**What runs:** `DAGValidator.validate(workflow)` at `dag_validator.py:26-41`.

**What's checked:**
- Acyclicity (no cycles in the DAG)
- Connectivity (unreachable nodes flagged as warnings)
- Edge integrity (source/target exist)
- Port cardinality (CONDITION has two outputs, etc.)
- Trigger uniqueness (exactly one TRIGGER node)
- Node config schema (per-node static config validation, e.g. `LoopNodeHandler` requires `collection` or `items_expression`)

**What's NOT checked:**
- **Variable reference resolution.** `_check_references` is not called. Design-time expressions are treated as opaque strings; they are not parsed or traversed.
- Schema of `$input` data — the input data doesn't exist yet at save time.
- Dynamic scope population — `$artifacts`, `$config`, etc. are not cross-referenced against producers.

**Failure mode:** `ValidationError` → controller returns `400` with the list of errors. The workflow is not persisted.

### 5.2 Checkpoint B — Run start

**When:** `StartRunUseCase._start_run` — `start_run_use_case.py:82-200`.

**What runs:**
1. Workflow existence check (`workflow_repo.get_by_id(workflow_id)` returns non-None).
2. Optional DAG re-validation — if `req.skip_validation=False` (default **True** — the API caller passes `skip_validation=True` to avoid re-running the save-time check). Runs `DAGValidator.validate(workflow)` again.
3. Optional input schema check — if `workflow.input_schema` is set AND `skip_validation=False`, runs `SchemaValidator.validate_input(req.input_data, workflow.input_schema)`. **This is the only place `$input` is statically type-checked.** If no `input_schema` is configured, the input is accepted as-is.
4. `DAGPreparer.prepare(workflow)` — builds `DAGInfo` (adjacency, predecessors, edge_map, entry/leaf nodes). This is topological-only; no expression evaluation.
5. Seeds `context.variables` with `$input`, `$workflow`, `$artifacts` (empty), and later `$run`.

**What's NOT checked:**
- Individual node input bindings (`node.input_binding`). They are not resolved; any references that depend on upstream node outputs can't be checked here anyway because those nodes haven't run yet.
- `$artifacts`, `$config`, dynamic keys — none exist yet.

**Failure mode:** `StartRunResult(error=...)` → controller returns `400` with the error code. The run is not created.

### 5.3 Checkpoint C — Task dispatch (the most important one for user expressions)

**When:** Every time a task is about to execute. This is the first and only place that actually evaluates user expressions against live data.

**What runs (per task):**
- For BUILTIN nodes (`CONDITION`, `SWITCH`, `LOOP`, `COMPUTE`, ...), the handler's own logic calls `variable_resolver.resolve(expression, source_data)`. Each handler decides which expressions to resolve and when (see e.g. `compute_handler.py`, `condition_handler.py`).
- For TASK nodes, `TaskNodeHandler.handle` calls `variable_resolver.resolve_mapping(node.input_binding, resolve_ctx)` to turn the declared `input_binding` dict into the concrete `resolved_input` that will be POSTed to the worker.
- For PENDING nodes (`INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, `FILE_UPLOAD`), the handler calls `resolve_mapping` to compute prefilled defaults.

**What's checked:** that every `{{$...}}` in the node's bindings resolves to a non-missing path under `run.context.variables`.

**Which scopes are live at this point:**
- `$input`, `$workflow`, `$run`, `$trigger` — guaranteed from run start.
- `$artifacts.<name>` — ONLY if an upstream task has completed and called `register_artifacts` with that name.
- `$<node_id>.output.<path>` — ONLY if `<node_id>` has already completed its handler (is in `context.completed_nodes`).
- `$loop.<key>` — ONLY if the task is inside a loop body AND the loop handler has pushed the current iteration's state onto `variables["loop"]`.
- `$config.<key>` — ONLY if something seeded it (nothing in the engine does today — so `$config.*` references always fail at task dispatch).

**Failure mode:** `VariableResolutionError` → wrapped as `ExpressionError` → caught in `OrchestrationEngine._execute_node_inner` and `SuccessorProcessor.process` → the task is marked `FAILED` with the expression message as `task.error` → fail propagates via `RunFailed`. **The run does not continue** in the default configuration.

### 5.4 Checkpoint D — Post-execution state write

**When:** immediately after a handler returns `NodeHandlerResult`.

**What runs:** `SuccessorProcessor.process` writes `state_updates` into `run.context.variables` and aliases node-id keys to node-name keys.

**What's checked:** not validation per se — but this is where new keys become visible to the resolver. Any downstream task dispatched after this write can see the new node's output. A task dispatched from the *same* handler result (e.g. a parallel fan-out) may or may not see prior siblings' output depending on ordering. See `docs/design/runtime-validation-pipeline.md` if that becomes relevant.

### 5.5 Summary table

| Checkpoint | When it runs | Scope of what's checked | Catches | Misses |
|---|---|---|---|---|
| **A — Workflow save** | `POST /workflows` | Static DAG shape | Cycles, port errors, disconnected nodes, schema | Expression references, input schema |
| **B — Run start** | `StartRunUseCase` | Run-level wiring | Workflow existence, optional DAG re-check, optional input_schema | Per-node expressions |
| **C — Task dispatch** | Each task's handler | `input_binding` / `resolve_mapping` resolution | Missing variables, unresolved `$...`, path traversal errors | Nothing — this is the last line of defense |
| **D — Post-execution** | After handler returns | `state_updates` write-out | Not a check; this is when new scope entries become visible | N/A |

**Key takeaway:** the system deliberately pushes expression validation to Checkpoint C because it is the *only* point at which dynamic scopes (`$artifacts`, node outputs, loop iteration state) are fully populated. Earlier checkpoints would either produce false positives (rejecting valid workflows that use dynamic references) or false negatives (passing workflows that will fail at runtime).

---

## 6. Loop scope deep-dive — how `LoopScope` and `$loop` relate

Because the user's original question lists "loop scope" as one of the four namespaces, it deserves its own section. There are actually **two different things** called "loop scope", and they serve different purposes.

### 6.1 The user-visible `$loop` dict

This is what a workflow author references as `{{$loop.index}}` or `{{$loop.item}}`. It lives at `run.context.variables["loop"]` and is rewritten by `LoopNodeHandler` on every iteration. It has fixed keys (`index`, `item`, `total`, `first`, `last`, `depth`) and always reflects the **innermost active iteration**. This is a runtime-populated scope but with a static key set.

### 6.2 The task-carried `scope_stack`

This is `Task.scope_stack: list[LoopScope] | None` (Phase 4c.4). Each body task inside an event-driven loop carries one `LoopScope` frame per enclosing loop, outermost first. It is:

- **Not expression-visible** — authors cannot write `{{$scope_stack[0].invocation_id}}`.
- **Consumed by the merge handler** (Phase 4c.4.4) to key quorum buffers by `(node_id, invocation_id, iter)` so merges inside nested loops re-arm per iteration instead of leaking state between outer iterations.
- **Deterministic** — the `invocation_id` is `uuid5(run_id, loop_node_id, enclosing)`, so same inputs always produce the same id (for replay dedupe) but nested loops differ per outer iter (for correctness).

### 6.3 Why the distinction matters

In the legacy (non-event-driven) path, a nested loop silently produces wrong `$loop` values — the inner loop's iteration overwrites the outer loop's. The user-visible symptom is that `{{$loop.index}}` inside an inner body always looks correct for the innermost layer but gives you no way to reference the outer.

In the event-driven path (`EVENT_DRIVEN_LOOP=true`, Phases 4c.4 / 4c.4.2 / 4c.4.3 / 4c.4.4), `$loop` still only shows the innermost iteration — but the task's `scope_stack` carries the full chain, and merge handlers correctly isolate per-iteration quorum. The spike §3.6 nested-loop uniqueness invariant is enforced.

A future phase (Phase 5+) may expose the outer scopes through a `$loop_stack` or `$outer_loop` namespace if authors need them, but that is explicitly out of scope today.

See `docs/design/loop-event-driven-spike.md` §3 and the Phase 4c.4 / 4c.4.2 / 4c.4.3 / 4c.4.4 rows in `event-driven-execution-plan.md` for the full rationale.

---

## 7. Resolver strategy chain — which strategy handles which scope

When an expression is resolved, the `VariableResolver` walks its strategies in order. Each strategy is given the source dict + the path and either returns a resolved value or raises `UNRESOLVED` so the next strategy can try.

Configured in `bootstrap.py:288-297`:

```python
resolver = VariableResolver(
    strategies=[
        FileBackedResolverStrategy(data_store_service, file_service_client),  # 1st
        MetadataResolverStrategy(),                                            # 2nd
        TraversalResolverStrategy(),                                           # 3rd
    ],
    monitor=monitor,
)
```

| Strategy | Handles | Raises on miss? |
|---|---|---|
| **FileBackedResolverStrategy** | Paths into a file_ref (i.e. `{"__file_ref": true, "file_id": ..., "extracted": {...}}`). Supports `__file_id`, `__content_type`, `__artifacts`, `__artifact.<name>` meta-keys. Supports OData single-row fetch for `array[index].field`. | No — returns UNRESOLVED so the next strategy can try |
| **MetadataResolverStrategy** | `$source.field` where the source is a file_ref and the field is in its pre-extracted `metadata.extracted` dict | No — returns UNRESOLVED |
| **TraversalResolverStrategy** | Plain nested dict/list traversal — `$<key>.<sub>[index].<field>`. This is what resolves `$input`, `$workflow`, `$run`, `$trigger`, `$loop`, `$artifacts`, `$<node_id>` in the common case. | **Yes** — raises `VariableResolutionError` if the final path is missing, since this is the last strategy in the chain |

**Implication for authors:** if your expression references a file-backed upstream output (e.g. `$input.email` where `$input` was stored as a CSV file_ref at run start), the FileBackedResolver handles it. If your expression references a plain dict (e.g. `$run.id`), the TraversalResolver handles it. You write both kinds identically — the chain handles the dispatch.

---

## 8. Practical guidance for workflow authors

1. **Use `$input`, `$workflow`, `$run`, `$trigger` freely.** They are always populated by run start.
2. **Use `$<upstream_node_id>.output.<field>`** once the upstream node has completed. The DAG guarantees execution order, so any successor can safely reference its predecessors.
3. **Use `$artifacts.<name>` only if an upstream node with `artifact_key: <name>` has executed.** If you conditionally skip the upload path, `$artifacts.<name>` will not be populated and downstream references will fail the task — either wire a fallback or gate the referencing node with a CONDITION.
4. **Avoid `$config.*`** — there is no engine hook that populates it today. It exists in the validator's allow-list for future use. Use `$input` to pass configuration.
5. **Inside a LOOP body, `$loop.index` / `$loop.item` always reflect the innermost iteration.** If you need the outer iteration's values, you cannot access them from the expression language today — restructure the loop or use a COMPUTE node with a `result_key` to bind them to a non-`$loop` variable.
6. **COMPUTE `result_key` lets you publish top-level variables.** If you find yourself repeatedly writing `{{$my_compute.output.result}}`, setting `result_key: "my_result"` lets downstream nodes write `{{$my_result}}` directly.
7. **If an expression fails at task dispatch, the task fails and the run may fail.** There is no silent fallback. Test your workflows with realistic input data before pushing to production.
8. **To exit a loop early, place a `LOOP_EXIT` node in the body branch you want to terminate on** (Phase 4c.4.5). Typical pattern: `LOOP → body_entry → CONDITION → (true) → LOOP_EXIT` / `(false) → continue the body`. Break is cooperative — in-flight parallel iterations finish before the loop terminates — and produces a `LoopCompleted(reason="break")` event. **Only works when `EVENT_DRIVEN_LOOP=true`**; with the flag off, `LOOP_EXIT` becomes a no-op (logged warning).

---

## 9. References to code and other docs

**Code:**
- `app/layer1_domain/services/variable_resolver.py` — resolver chain, base strategies
- `app/layer1_domain/services/file_backed_resolver.py` — file-ref resolution + `$metadata` + `$artifacts` meta-keys
- `app/layer1_domain/entities/data_reference.py:83` — `register_artifacts()`
- `app/layer1_domain/services/dag_validator.py` — Checkpoint A
- `app/layer2_application/features/start_run/use_cases/start_run_use_case.py:82-200` — Checkpoint B + `context.variables` seeding
- `app/layer1_domain/services/successor_processor.py` — Checkpoint D (state_updates → variables write-back)
- `app/layer1_domain/services/node_handlers/*.py` — per-handler `state_updates` shape
- `app/layer1_domain/value_objects/loop_scope.py` — `LoopScope` + `derive_invocation_id`
- `app/layer1_domain/entities/task.py` — `Task.scope_stack`, `get_innermost_scope`, `with_pushed_scope`

**Related docs:**
- `docs/design/loop-event-driven-spike.md` — the Phase 3 spike that designed `LoopScope` and the nested-loop invariants
- `docs/design/event-driven-execution-plan.md` — Phase 4c.4 / 4c.4.2 / 4c.4.3 / 4c.4.4 rows for the rollout history
- `docs/design/event-driven-gap-analysis.md` — the "Nested-loop support" and "Merge quorum inside loop body" rows for the before/after state
- `docs/design/runtime-validation-pipeline.md` — more detail on Checkpoint C runtime resolution (if present)
- `docs/design/context-validator-resolver-migration.md` — historical design for the resolver chain
