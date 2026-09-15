# INPUT "Template Preview": data queries return empty (two distinct bugs)

**Status:** Bug #2 + Bug #1 both FIXED + live-verified (2026-06-09)
**Date:** 2026-06-09
**Repro workflow:** `10dea6aa-4837-4f87-be6b-47dd65bb643f` ("test-workflow")
**INPUT node:** `66a051c9-5531-40ff-b848-3677e1a09a4f` ("Template Preview")
**Repro run:** `ee657830-873b-46a6-8c9c-e8636afc5ad1`

## Symptom (as reported)

The "Template Preview" INPUT node has default values for the **root** (scalar fields)
and for **nested tables**, all bound to the upstream HTTP TASK output. When the data is
queried, it comes back **empty**.

## TL;DR

There are **two independent bugs**, and they compound:

1. **Bug #2 (primary — read side):** the OData endpoint the FE uses to render *every*
   table/grid (`GET …/artifacts/{artifact_id}/odata`) reads node data from the **run
   aggregate** (`run_repo`), but INPUT-node artifacts are **view-only** (projector/view
   store is authoritative since the Phase-3b engine split). The aggregate has no file_ref
   for the node → it returns **`404 ARTIFACT_NOT_FOUND` for *all* artifacts**, including
   the ones that are actually populated. This is why the whole preview looks empty.
2. **Bug #1 (secondary — write/provision side):** the resolved **scalar** defaults are
   never seeded into the root artifact, so even once Bug #2 is fixed the root (scalar
   tabs) stays empty.

The nested **tables themselves are seeded correctly** (`fieldSections`=129 rows,
`approvalProcesses`=1 row; `validationRules` is empty only because its source array is
empty) — they're just unreadable through the FE's read path because of Bug #2.

## Evidence (live)

A run was driven to the pending INPUT node.

Expression resolution fired completely (control-plane log): every
`{{$0e48f1dd-….response_body.data.X}}` resolved against the TASK output — `name`,
`status`, `createdAt`, `templateTrigger.*` (scalars), `fields`→129 dicts,
`approvalProcesses`→1 dict, `rules`→`[]`.

| Read path | Source | Result |
|---|---|---|
| `GET …/nodes/{n}/data` (descriptor tree) | **view store** | ✅ root + `fieldSections`(129) + `approvalProcesses`(1) + `validationRules`(0) |
| `GET …/nodes/{n}/artifacts/{aid}/odata` (**FE row read**) | **run_repo aggregate** | ❌ **404 ARTIFACT_NOT_FOUND for every `aid`** (root, fieldSections, approvalProcesses, validationRules) — still 404 minutes later (not eventual-consistency) |
| Provisioner log | — | `root.csv cols=['__row_id'] seed_rows=0`; `fieldSections seed_rows=129`; `approvalProcesses seed_rows=1`; `validationRules seed_rows=0` |

Source-usage grep across read use-cases:

```
get_node_data        : view_store_refs=4  run_repo_refs=0   (cut over)
query_node_artifact  : view_store_refs=0  run_repo_refs=4   (straggler ← Bug #2)
get_node_output      : view_store_refs=0  run_repo_refs=4   (also aggregate-only)
edit_node_data       : view_store_refs=4  run_repo_refs=4
```

---

## Bug #2 — OData read path reads the wrong source (PRIMARY)

`QueryNodeArtifactUseCase` (serves `GET /runs/{run}/nodes/{node}/artifacts/{aid}/odata`,
the only path the FE uses to fetch table rows):

```python
run = await self.run_repo.aget_by_id(command.run_id)          # ← run AGGREGATE
file_ref = run.context.variables.get(command.node_id)
if not is_file_ref(file_ref):
    return self._error(ErrorCode.ARTIFACT_NOT_FOUND, …)        # ← every call lands here
artifact = find_artifact(file_ref, command.artifact_id)
```

`get_node_data` was migrated to `IRunContextViewStore`
([[project_node_data_view_store_cutover]]); `query_node_artifact` was **not**. With the
Phase-3b engine split the projector/view is authoritative and node outputs/provisioned
refs are *view-only* — they are not written back onto the run aggregate
([[project_engine_split_phase3b]]). So `run.context.variables[node_id]` on the aggregate
is not a file_ref and **every** artifact 404s. `find_artifact` is source-agnostic and
resolves the dotted ids correctly when given the view tree — the only fault is the source.

### Fix (IMPLEMENTED 2026-06-09, live-verified)

`QueryNodeArtifactUseCase` now resolves the node's `__file_ref` from the **view store**
with the same precedence `GET /data` / PATCH use, instead of the run aggregate:

```python
view = self.run_context_view_store.get(run_id)            # was run_repo.aget_by_id
task = <latest task for node, or the explicit ?task_id=>  # alist_by_run + find_latest_task_for_node
output_ref = view.variables.get(node_id)                  # working copy (authoritative once edited)
input_ref  = getattr(task, "resolved_input", None)        # frozen input — the fallback
file_ref   = output_ref if is_file_ref(output_ref) else input_ref
```

The **`resolved_input` fallback is essential**, discovered during live verification:
after a cold control-plane restart the working copy `view.variables[node_id]` is **empty**
— the INPUT node's provisioned working-copy ref is *not* durably replayed from the event
log (only `task.resolved_input`, persisted on the task, survives). A first cut that read
only the working copy still 404'd post-restart; the fallback fixes it and matches
`EditNodeDataUseCase._current_working_ref` precedence (output → input → empty).

- Constructor: `run_repo: IRunRepository` → `run_context_view_store: IRunContextViewStore`
  (auto-wired by name in `bootstrap.py`). `find_artifact` + OData forward unchanged.
- Tenancy guarantee preserved (artifact must exist in the node's descriptor tree).
- Regression test added: `test_query_node_artifact_resolves_via_resolved_input_fallback`
  (working copy empty, ref only on `resolved_input` → 200).

**Verification (run `ee657830…`, after a cold CP restart):**

| Artifact | Before | After |
|---|---|---|
| `tabs.tab_4.fieldSections` | 404 | **200, total_count=129** |
| `tabs.tab_5.approvalProcesses` | 404 | **200, total_count=1** |
| `tabs.tab_3.validationRules` | 404 | 200, total_count=0 (source empty) |
| `__root__` | 404 | 200, total_count=0 (**Bug #1**, still open) |

Tests: `test_unit_e_endpoints.py` 15 passed / 3 failed (the 3 `test_rerun_run_*` failures are
**pre-existing** — verified failing on the unmodified file too).

**Still TODO:** audit `get_node_output` — same aggregate-only source (`run_repo_refs=4`),
likely the same latent gap for any view-only node output.

> Note: a *pending, unedited* node having an empty working copy after replay (while
> `resolved_input` persists) is **by design** — there is no produced output yet, so the
> empty durable output projection is expected and `resolved_input` is the intended
> source. A *completed* node's output, by contrast, IS durably projected via
> `NodeOutputProduced` and survives cold start (verified: the run's COMPLETED TASK node
> presents a populated `output` ref post-restart) — so the fallback never fires for it
> and never serves a TASK node's input in place of its output. The fallback is therefore
> correct for all node types, not just INPUT.

---

## Bug #1 — resolved scalar defaults never seeded into the root artifact (SECONDARY)

All scalars live nested under object/tab wrappers (`tabs › tab_1`, `tabs › tab_2`,
`outputType:"object"`). Two services walk the schema inconsistently:

- `walk_schema_artifacts()` **recurses** object containers → finds nested **array**
  artifacts (`tabs.tab_4.fieldSections`) and seeds them. ✔
- `root_scalar_columns()` / `root_scalar_defaults()` inspect **top-level only**. The lone
  top-level item is `tabs` (object) → skipped → `scalar_cols=[]`, `scalar_defaults={}` →
  root CSV minted header-only (`__row_id`, 0 rows). ✘

`root_scalar_columns`' docstring concedes it: *"Nested scalars inside container wrappers …
are intentionally skipped — object containers are currently treated as passthrough."* The
passthrough lifts nothing for scalars.

**Can't be patched after provisioning:** `submit_node` takes no body (*"PATCH /data is the
sole write path"*), and the scalar fields are **`readonly:true`** display-only prefills —
the FE never PATCHes them. If they aren't seeded at provision time, nothing ever writes
them.

**Collision constraint:** `created_at`/`updated_at` appear in *both* tab_1 (template) and
tab_2 (trigger) with different values, so a single flat root CSV keyed by leaf name can't
hold both.

### Fix (IMPLEMENTED 2026-06-09, live-verified — Option A)

Object containers with **direct scalar children** now become their own `object_field`
artifacts, mirroring the array-under-object treatment:

- `schema_artifact_walker`: `SchemaArtifact` gains a `kind` field (`"array"` | `"object"`).
  `_walk` emits an `object` artifact at the container's dotted path (`tabs.tab_1`,
  `tabs.tab_2`) when it has direct scalar children — columns = those scalar keys, a single
  `default_rows` row = the (already-resolved) scalar defaults.
- `schema_artifact_provisioner`: provisions `kind == "object"` artifacts as `object_field`
  CSVs seeded with that one row; sort is `(depth, object-before-array)` so a mixed
  container's object parent exists before its array child attaches.
- Read side unchanged — `build_descriptor_tree` already recurses children of any
  `artifact_type`. Top-level scalars still go to `__root__` (unchanged).

Collision-free (each tab is its own CSV) and consistent with the existing dotted
nested-array artifacts. Considered but rejected: **Option B** (recurse scalars into the
root CSV with dotted keys — forces a leaf↔dotted bridge on the FE) and the **non-viable**
flat-leaf-key root (drops a colliding `created_at`/`updated_at`).

**Verification (fresh run `736d6b1d…`, `EXECUTOR_MODE=in_process`):**

provisioner log → `tab_1.csv seed_rows=1`, `tab_2.csv seed_rows=1`; OData:

| Artifact | Result |
|---|---|
| `tabs.tab_1` | `total_count=1` → `{templateName:"hihihi-done-quametmoi", created_at:"…09:02:37", status:"ACTIVE", templateDescription:"hihihihi", …}` |
| `tabs.tab_2` | `total_count=1` → `{triggerId:"testttt-jj123", created_at:"…09:03:24", changeType:"CREATE", …}` |
| `tabs.tab_4.fieldSections` | `total_count=129` (no regression) |
| `tabs.tab_5.approvalProcesses` | `total_count=1` |
| `__root__` | `total_count=0` (correct — no top-level scalars; the scalars live in tab_1/tab_2) |

`created_at` differs across the two tabs (`09:02:37` vs `09:03:24`) — the collision the flat
root CSV could not represent. Tests: `test_schema_artifact_walker.py` +5 cases,
`test_schema_artifact_provisioner.py` +1 case (36 pass total).

> **Local-dev note:** new runs need `EXECUTOR_MODE=in_process`. In the default/shadow mode a
> double-dispatch races an empty-input dispatch that fails the HTTP TASK with `'url' is
> required` ([[project_executor_mode_resolution_gap]]) — unrelated to these fixes, but it
> intermittently fails the run before the INPUT node is reached.

---

## Status

Both fixed + live-verified (backend). **Remaining TODO:** audit `get_node_output` (Bug #2's
sibling — same aggregate-only source).

### FE-contract for Bug #1 — CONFIRMED (Option A)

Option A relocates nested scalars to per-tab `object_field` artifacts (`tabs.tab_1`,
`tabs.tab_2`) and leaves `__root__` empty. The values are queryable at those dotted
artifacts and appear under `data.root.children` in `GET /data`. **The product owner
confirmed (2026-06-09) the FE reads object-wrapped scalars from the per-tab dotted
artifacts** — same addressing it already uses for tables — so Option A is correct and no
`__root__`/Option-B follow-up is needed.

**Blast radius:** this changes provisioning for *every* INPUT/HUMAN_ACTION schema with
object-wrapped scalars (shared `walk_schema_artifacts` / `provision_schema_artifacts`), not
only this workflow. Any existing flow whose FE expects nested scalars in `__root__` would see
a changed artifact tree.

## Repro steps (original symptom, for reference)

1. CP `:8001`, executor `:8004`, http-request-worker `:35000` up (`EXECUTOR_MODE=in_process`).
2. `POST /api/v1/runs {workflow_id: 10dea6aa-…}`.
3. Wait for the TASK to COMPLETE and INPUT to reach `PENDING` (remote file-service uploads
   take ~tens of seconds — **not** a hang).
4. `GET …/nodes/66a051c9-…/artifacts/tabs.tab_1/odata` → before: 404 (Bug #2) / empty root
   (Bug #1); after: the resolved scalar row.
