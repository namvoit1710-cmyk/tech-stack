# Dissolving `layer2_application/services/`

**Status:** design proposal (not started)
**Date:** 2026-06-18
**Scope:** `app/layer2_application/services/` — 71 files, ~16k LOC, 208 dependent files, 813 imports.

---

## 0. TL;DR

The domain rule the user set — *clean architecture has no `services/`; every module carries the
meaning of the domain itself* — applies to `layer2_application` too. The monolithic `services/`
bucket is a **technical** grouping; it must be replaced by **domain-/concept-named packages**
(the same way `features/` already groups by use case).

Two destinations, by nature of each module:

- **Pure modules** (no I/O collaborators) → **`layer1_domain`** as concept modules. This is a
  continuation of the decide/evolve seam refactor (slices 1–5 already moved `fanout`,
  `event_id_helper`, `loop_topology` down). The projector (`apply_event`) move is the `evolve`
  half that doc §2.1 explicitly anticipated.
- **I/O / orchestration modules** → **concept-named packages under `layer2_application`**
  (`orchestration/`, `node_handlers/`, `resolution/`, `data_access/`, `events/`,
  `workflow_definition/`, `read_model/`) — never a generic `services/`.

**Mechanics are already proven:** the re-export shim pattern (used for `event_id_helper` /
`fanout`) lets each cluster move without breaking its importers; a scoped import-rewrite then
deletes the shim. **Staged, one cohesive cluster per slice, suite as oracle, each slice
independently green and revertible** — identical philosophy to the decide/evolve slices.

This is a **multi-week effort** (813 imports to repoint). It is NOT a single sweep.

---

## 1. The target structure

```
app/layer2_application/
├── errors.py  generation_guard.py  run_save_retry.py  run_status_reader.py   # (root cross-cutting — unchanged)
├── features/            # use cases (unchanged)
├── interfaces/  contracts/  repositories/  providers/  orchestration_queue/  # (unchanged)
├── orchestration/       # engine + run lifecycle (was the services hub)
│   ├── orchestration_engine.py  run_scope_manager.py  node_execution_runner.py
│   ├── successor_processor.py  child_workflow_coordinator.py  loop_iteration_driver.py
│   └── loops/event_driven_loop_driver.py
├── node_handlers/       # the 27-handler family (lifted out of services/ intact)
├── resolution/          # variable_resolver.py  file_backed_resolver.py
├── data_access/         # data_store_service.py  schema_artifact_provisioner.py  row_path_resolver.py  csv_converter.py
├── events/              # event_emitter.py  event_collector.py  outbox_awaiter.py  stage_recorder.py
├── workflow_definition/ # node_spec_resolver.py  workflow_snapshot_loader.py  workflow_version_pinner.py  node_config_validator.py  node_definition_resolver.py
└── read_model/          # node_data_projection.py  node_status_aggregator.py  node_data_helpers.py(*)  schema_validator.py(*)

app/layer1_domain/       # pure logic pushed down (siblings of events.py / fanout.py / loop_topology.py)
├── topology.py          # ← topology_builder.py  dag_traversal.py
├── value_comparison.py  # ← value_comparator.py
├── csv_ops.py           # ← csv_ops.py (pure byte-level CSV)
├── schema_artifacts.py  # ← schema_artifact_walker.py
├── node_input_etag.py   # ← node_input_etag_calculator.py
└── run_projection.py    # ← projector/{run_state_projector, snapshot_codec, snapshot_shadow_comparator}  (the `evolve` half)
```

(*) `node_data_helpers` / `schema_validator` are pure and could go to layer1 instead of
`read_model/`; decided per-slice. Package names are a proposal — the key decision is **concept
packages, not `services/`**; exact names can be adjusted in review.

**`approval_process_transformer.py`** is used by exactly one feature (`get_approval_process`) →
fold it into that feature dir rather than a shared package. **`workflow_change_detector.py`** is
a tiny pure diff util → layer1 or `read_model/` per-slice.

---

## 2. Module → destination map (by cluster)

| Cluster | Modules | Destination | Pure? |
| --- | --- | --- | :---: |
| Pure graph | `topology_builder`, `dag_traversal` | `layer1_domain/topology.py` | ✅ |
| Pure rules | `value_comparator` | `layer1_domain/value_comparison.py` | ✅ |
| Pure data-format | `csv_ops` | `layer1_domain/csv_ops.py` | ✅ |
| Pure schema | `schema_artifact_walker` | `layer1_domain/schema_artifacts.py` | ✅ |
| Pure identity | `node_input_etag_calculator` | `layer1_domain/node_input_etag.py` | ✅ |
| **Projector (`evolve`)** | `projector/*` (3) | `layer1_domain/run_projection.py` | ✅ |
| Node handlers | `node_handlers/*` (27) | `layer2/node_handlers/` | mixed |
| Orchestration hub | `orchestration_engine`, `run_scope_manager`, `node_execution_runner`, `successor_processor`, `child_workflow_coordinator`, `loop_iteration_driver`, `loop_driver/*` | `layer2/orchestration/` | I/O |
| Resolution | `variable_resolver`, `file_backed_resolver` | `layer2/resolution/` | I/O |
| Data access | `data_store_service`, `schema_artifact_provisioner`, `row_path_resolver`, `csv_converter` | `layer2/data_access/` | mixed |
| Events | `event_emitter`, `event_collector`, `outbox_awaiter`, `stage_recorder` | `layer2/events/` | mixed |
| Workflow definition | `node_spec_resolver`, `workflow_snapshot_loader`, `workflow_version_pinner`, `node_config_validator`, `node_definition_resolver` | `layer2/workflow_definition/` | mixed |
| Read model | `node_data_projection`, `node_status_aggregator`, `node_data_helpers`, `schema_validator` | `layer2/read_model/` (or layer1 for the pure two) | mixed |
| Transforms | `approval_process_transformer` | fold into `features/get_approval_process/` | ✅ |
| Misc | `workflow_change_detector` | `layer1_domain/` or `read_model/` | ✅ |
| Already moved (shims) | `event_id_helper`, `dispatcher/fanout` | delete shims; repoint to layer1 | — |

---

## 3. Coupling — why this order

- **Hubs (move LAST):** `orchestration_engine` imports ~17 services; `node_handlers/` has 30
  internal importers; `task_factory` (22) and `variable_resolver` (21) are reused everywhere.
- **Leaves (move FIRST):** ~34 modules have zero internal importers (pure utilities, validators,
  resolvers, individual handlers).
- **External importers (208 files):** tests/ (125), features/ (29), layer4 (7), layer3 (1). Most
  external coupling is to `stage_recorder` (13), `event_id_helper` (13), `topology_builder` (7),
  `successor_processor` (6), `workflow_snapshot_loader` (5).

Move low-coupling/pure clusters first to validate the pattern cheaply; move the orchestration hub
(engine + processors + drivers, a tight 6-module cluster) as the final, highest-risk slice.

---

## 4. Migration mechanics (per slice)

The proven shim pattern, applied per cluster:

1. **Move** the cluster's modules to their destination package (`git mv` content unchanged).
2. **Fix internal imports** within the moved modules (their references to each other / to
   already-moved clusters).
3. **Leave a re-export shim** at each old `services/X.py` path
   (`from app.layer2_application.<pkg>.X import *  # noqa: F401`) so the cluster's ~N importers
   keep working untouched this slice.
4. **Run the touched test set** → green (byte-identical: only import paths changed).
5. **Repoint importers** of this cluster across the repo (scoped find/replace of the old path),
   then **delete the shim**. This can be the tail of the same slice or a dedicated cleanup slice
   per cluster — whichever keeps the diff reviewable. **Sweep the WHOLE service dir, not just
   `app/` + `tests/`** — `bootstrap.py` (and other root-level files) live outside `app/` and are
   easy to miss; a missed importer surfaces as a `--collect-only` `ModuleNotFoundError`.
6. **Verify** `pytest tests/ --collect-only` (import gate) + the cluster's unit/oracle tests +
   one isolated in-memory e2e. Each slice is its own commit, independently revertible.

**Bootstrap** (`build_app_container`, `bootstrap.py`) is the one composition root that constructs
these modules — update its imports per slice (it imports the concrete classes by path).

**Pure→layer1 cycle check:** every layer1 move must pass `--collect-only` (no
`workflow_run → … → workflow_run` cycle); annotate cross-entity types under `TYPE_CHECKING` as the
decide/evolve slices did.

---

## 5. Slice plan

| Slice | Cluster | Move to | Risk | Verify |
| --- | --- | --- | :---: | --- |
| 0 | Remove already-relocated shims (`event_id_helper`, `dispatcher/`, `loop_topology` re-export) | repoint ~25 importers → layer1; delete shims + `services/dispatcher/` | low | collect-only; fanout/loop oracles |
| 1 | Pure utilities (`topology_builder`, `dag_traversal`, `value_comparator`, `csv_ops`, `schema_artifact_walker`, `node_input_etag_calculator`) | `layer1_domain/*` | low | their unit tests + collect-only |
| 2 | Projector (`evolve`) | `layer1_domain/run_projection.py` | med | projector rebuild + snapshot parity tests |
| 3 | `node_handlers/` (27) | `layer2/node_handlers/` | med | all handler unit tests + node e2e |
| 4 | Resolution / data_access / events / workflow_definition / read_model (concept packages) | `layer2/<concept>/` | med | per-cluster unit + output/state e2e |
| 5 | Orchestration hub (engine + processors + drivers) | `layer2/orchestration/` | **high** | full unit suite + all isolated in-memory e2e |
| 6 | Delete empty `services/`; final import sweep; bootstrap tidy | — | low | full unit suite + collect-only |

**Test oracle:** system `pytest` (`/usr/local/bin/pytest`). Per slice: `--collect-only` import
gate, the cluster's unit tests, and **isolated in-memory** integration e2e only
(`MemoryRunRepository`). Do **NOT** run `build_app_container()` / HANA-backed integration (shared
SAP HANA Cloud dev DB). Final gate each slice: full `tests/unit/`.

---

## 6. Honest caveats

- **Big.** 813 imports across 208 files. Budget multiple sessions; each slice is independently
  shippable so it can pause/resume safely.
- **No behaviour change is the contract.** Pure file moves + import rewrites. If a test's
  *expectation* must change to pass, stop — something was cut wrong; revert the slice.
- **The orchestration hub (slice 5) is the real risk.** Six tightly-coupled I/O modules + the
  engine; move them together, last, behind shims, with the full e2e set as oracle.
- **Naming is a proposal.** The non-negotiable is *no `services/` bucket; concept packages by
  domain meaning*. Exact package names (`orchestration/` vs `engine/`, `data_access/` vs
  `data_store/`) are open to your preference and can be set before slice 1.
- **Interplay with the decide/evolve refactor:** slices 1–2 here (pure utils + projector→layer1)
  directly advance that doc's §2.1 (`evolve` to domain) and §6 (one-state convergence groundwork).

---

## 7. Recommended first step

Slice 0 (delete the three relocation shims I just introduced and repoint their ~25 importers to
layer1) — smallest, lowest-risk, removes the immediate `services/` artifacts from the
decide/evolve work, and proves the import-rewrite-then-delete-shim mechanic end-to-end before the
larger clusters.
