# Node-data endpoints — view-store cutover

> Migrates `GetNodeDataUseCase` and `EditNodeDataUseCase` off direct
> `run.context.variables` aggregate reads onto `IRunContextViewStore`,
> completing the Phase 3b.2a / 3b.3 read-side cutover for the two
> use cases that were missed. Shipped 2026-04-23.

## 1. Why

Phase 3b.2a (shipped 2026-04-22) deleted the
`run.context.variables[node_id] = new_ref` aggregate write from
`EditNodeDataUseCase` on the premise that the projector reducer
handles `NodeDataEdited` with the same semantics as
`NodeOutputProduced`. Phase 3b.3 then deleted the live/comparator
view stores, making `CachedProjectorRunContextViewStore` authoritative.

The companion **read** migration for these two use cases never
happened. Symptoms:

- `GetNodeDataUseCase.execute` read `run.context.variables.get(node_id)`
  from `run_repo.get_by_id(...)`. Aggregate is frozen at dispatch time,
  so `GET /data?side=output` never reflected a PATCH.
- `EditNodeDataUseCase._current_working_ref` read the same stale
  aggregate for base-state + If-Match compute. Consecutive PATCHes
  re-based against the initial dispatch state — each PATCH silently
  discarded prior edits, and the ETag returned by PATCH was
  immediately unusable for the next PATCH (412).

Matching migration precedent: the seven status-checker use cases in
Phase 3b.2c (`cancel_run`, `resume_run`, `resume_pending_task`,
`sweep_timed_out_child_runs`, `pause_run`, `rerun_task`, `trigger_node`)
all route status reads through `read_authoritative_status(run, view_store)`.
`GetRunContextUseCase` reads `view_store.get(run_id).variables`.
`get_node_data` + `edit_node_data` were simply omitted — probably
because they landed under the node-input-review-edit API work in
parallel with the engine split.

## 2. What shipped

### 2.1 `GetNodeDataUseCase` — full migration

- Dropped `run_repo: IRunRepository` from `__init__`; added
  `run_context_view_store: IRunContextViewStore`.
- `execute` loads the view via `self.run_context_view_store.get(command.run_id)`;
  `output_ref = view.variables.get(node_id)`; port/edges come from
  `view.last_output_ports` / `view.last_taken_edge_ports`.
- `_produced_artifacts(view, node_id)` — reads the `$artifacts`
  registry from `view.variables["artifacts"]`.

### 2.2 `EditNodeDataUseCase` — base-state + If-Match migration

- Added `run_context_view_store: IRunContextViewStore` to `__init__`;
  kept `run_repo` as load-only (engine emission needs a `WorkflowRun`
  instance).
- `_current_working_ref(run_id, node_id)` — reads from
  `self._view_store.get(run_id).variables` instead of
  `run.context.variables`. The empty-ref path returns a fresh
  `build_file_ref(file_id="")` instead of mutating aggregate
  variables in place.

### 2.3 `compute_node_state` — polymorphic over view + aggregate

`node_data_projection.compute_node_state(source, node_id, task)`
already accepted "something with `.context.skipped_nodes`". Extended
to prefer top-level `.skipped_nodes` / `.completed_nodes` (the view
shape), falling back to `.context.*` (the aggregate shape). Keeps the
one remaining aggregate caller (`edit_node_data_use_case._dispatch_rerun`)
working unchanged while letting `get_node_data` pass a view directly.

### 2.4 `CachedProjectorRunContextViewStore._aggregate_fallback`

Added to close the "empty event log" gap the design doc at
`phase-3b-inline-write-removal.md` §3.1 referenced but wasn't
implemented. Before this PR, `.get(run_id)` returned `None` whenever
the event log had no envelopes for the run. Post-migration that
manifests as `GET /data` returning 404 for any aggregate-seeded test
fixture and for production runs during the pre-`RunStarted` window.

New behavior: after the full-log replay path runs and finds no
envelopes, the view store calls `_aggregate_fallback(run_id)` which
returns `RunContextSnapshot.from_run(run)` when a `run_repo` is
wired. The fallback view is **not cached** — the next `.get()` goes
through the replay path again, so as soon as events start flowing
the store migrates cleanly to the projector-backed view.

Also updates
`test_cached_projector_view_store.TestCachedStoreGetByRootId::test_get_by_root_id_skips_runs_without_events`
→ renamed to `test_get_by_root_id_falls_back_to_aggregate_for_runs_without_events`
to match the new contract.

### 2.5 `orchestration_engine.emit_node_data_edited` — docstring

Rewrote the stale "emit before writing to `run.context.variables`"
docstring. The write is gone; the docstring now states the projector
reducer is the sole applier.

## 3. Tests

- **Unit regression** (`tests/unit/test_get_node_data.py`) — 3 new
  tests set up a deliberate divergence where the aggregate and view
  store hold different variables, and assert every GET side
  (`output_ref`, `last_output_ports`, `last_taken_edge_ports`,
  `$artifacts` registry) reads the view store. These tests failed on
  `main` with TypeError when the use case refused `run_repo` (post
  signature change), confirming the migration is load-bearing.

- **Fixture migration** — `_build_uc()` in the same file now wires
  `LiveViewStoreDouble(run_repo)` so existing aggregate-seeded tests
  continue to work through the view protocol.

- **Integration** (`tests/integration/test_node_read_endpoints.py`) —
  4 tests were failing post-migration because the real
  `CachedProjectorRunContextViewStore` returned `None` for
  aggregate-seeded runs with no event log. Fixed by the
  `_aggregate_fallback` addition. No test body changes needed.

- **Full suite** — 1854 passed / 127 skipped / 0 failed / 0 errors.

## 4. Contract changes (call sites)

- `GetNodeDataUseCase.__init__` — `run_repo` replaced by
  `run_context_view_store`. Smart injection picks up the new parameter
  from `bootstrap.available_dependencies` line 593 automatically;
  no bootstrap change required.
- `EditNodeDataUseCase.__init__` — adds `run_context_view_store` as
  a required parameter. Same smart-injection pickup.
- `EditNodeDataUseCase._current_working_ref` — signature shifted from
  `(run, node_id)` to `(run_id, node_id)`.

## 5. Follow-ups not in scope

- Migrating `edit_node_data_use_case._dispatch_rerun`'s `run`-level
  reads (e.g. `dag_info` pulled from the aggregate). Those aren't
  bugs today because the aggregate's DAG state is static after
  dispatch — deferred to whenever Phase 2a's engine-read migration
  extends into the rerun path.
- `GetNodeSchemaUseCase` still consumes the aggregate for its own
  `compute_node_state` call — matches the Pattern A migration matrix
  but wasn't inside this PR's scope.

## 6. Rollback

Revert the PR. The `_aggregate_fallback` addition is
backwards-compatible (old behavior = return None when no events; new
behavior = attempt fallback, return None when no run_repo wired or
run unknown). Test renames can stay or revert together with the
production change.
