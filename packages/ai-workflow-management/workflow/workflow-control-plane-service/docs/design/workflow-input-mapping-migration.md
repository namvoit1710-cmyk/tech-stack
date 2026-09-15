# Migrating WORKFLOW nodes: `input_binding` → `input_mapping`

**Status:** reference / runbook — the code, tooling, and tests already exist.
**Scope:** the WORKFLOW (sub-workflow) node slice of the NodeSpec normalization.
**See also:** [`node-spec-single-source.md`](./node-spec-single-source.md) §9
(overall migration), [`node-spec-fe-changes.md`](./node-spec-fe-changes.md)
(FE coordination), and the runbook
[`db/migrations/042_normalize_node_config.md`](../../db/migrations/042_normalize_node_config.md)
(covers every node type, not just WORKFLOW).

This document explains, end-to-end, how an old workflow that stored a
WORKFLOW node's sub-workflow input mapping under the legacy key
`input_binding` becomes a workflow that stores it under the canonical key
`input_mapping`, and how to execute that migration safely.

---

## 1. What changed and why

A WORKFLOW node runs another workflow as a child. The mapping from the
parent's data/expressions into the child's inputs was historically a config
key literally named **`input_binding`**.

That collided with the engine entity field `WorkflowNode.input_binding`
(`app/layer1_domain/entities/workflow.py:48`) — a *generic* runtime-binding
field that every node type carries. A config key and an entity field with the
same name made the stored JSON ambiguous: was `input_binding` in a node dict
the engine field or the WORKFLOW config key?

The redesign renames the **config key** to **`input_mapping`** and leaves the
entity field `input_binding` for what it always was (generic bindings). The
canonical key is declared once, in the node's spec:

`app/layer1_domain/node_spec/specs/workflow_node.py:45-55`
```python
ConfigField(
    key="input_mapping", type=FieldType.OBJECT, label="Input Mapping",
    widget=Widget.ARRAY_TABLE, store_as="dict", expression=True,
    item_fields=(
        ConfigField(key="param_name",  type=FieldType.STRING, ...),
        ConfigField(key="param_value", type=FieldType.STRING, expression=True, ...),
    ),
)
```

The change shipped **clean-break**: the handler reads `input_mapping` only and
has **no `input_binding` fallback**.

`app/layer2_application/services/node_handlers/workflow_handler.py:41-58`
```python
# Canonical keys only (NodeSpec Phase 3): input_mapping is always
# a dict — the array-of-rows form value folds at split time.
cfg = WorkflowNodeConfig.from_node(node)
...
input_mapping = cfg.input_mapping
```

**Consequence:** any stored workflow or frozen run snapshot that still carries
the legacy `input_binding` config key is invisible to the handler — its child
inputs resolve to `{}` — until it is normalized once to `input_mapping`.

---

## 2. Old shape → new shape

**Before** (legacy stored WORKFLOW node):
```json
{
  "node_type": "WORKFLOW",
  "config": {
    "workflow_id": "wf-abc",
    "input_binding": [
      {"param_name": "customerId", "param_value": "{{$input.id}}"},
      {"param_name": "region",     "param_value": "{{$nodeA.output.region}}"}
    ]
  },
  "input_binding": {}
}
```

**After** (normalized):
```json
{
  "node_type": "WORKFLOW",
  "config": {
    "workflow_id": "wf-abc",
    "input_mapping": {
      "customerId": "{{$input.id}}",
      "region":     "{{$nodeA.output.region}}"
    }
  },
  "input_binding": {}
}
```

Two transforms happen together:
1. **Key rename:** `input_binding` → `input_mapping`.
2. **Row-array fold:** the `[{param_name, param_value}]` rows fold into a
   `{param: expression}` dict (the shape the engine consumes). API-authored
   nodes that already stored a dict pass through untouched.

---

## 3. How the transform works

The migration is an **application-level** data transform (not SQL). Node config
is stored as JSON in `AIW_WORKFLOWS.NODES` (NCLOB) and inside frozen run
snapshots, so the tool reads each document, rewrites it in memory, and saves it
back. Logic: `tools/normalize_node_config.py` → `normalize_node_dict()`.

For a WORKFLOW node:

1. **Collapse to FE view.** `merge_parameters(config, input_binding)` merges
   both engine fields back into one flat dict
   (`app/layer1_domain/node_spec/parameter_mapper.py:38-49`). Because the legacy
   key is literally named `input_binding`, it appears in this merged dict
   regardless of whether it sat in `config` or in the entity-level field.

2. **Rename.** `normalize_node_config.py:167-171`:
   ```python
   elif node_type == "WORKFLOW":
       if "input_binding" in merged:
           merged.setdefault("input_mapping", merged["input_binding"])
           del merged["input_binding"]
           report.bump("workflow_input_binding_renamed")
   ```

3. **Re-split through the spec.** `split_parameters(WORKFLOW_SPEC, merged)`
   routes `input_mapping` into `config` (it is a declared config field) and
   `_apply_store_as` folds the row array into a dict because the field declares
   `store_as="dict"` (`parameter_mapper.py:52-66`). The result is exactly the
   shape `WorkflowNodeConfig.from_node` reads.

The transform is **idempotent** — a node already in canonical form re-splits to
itself, so a second run produces an empty diff.

**Coverage:** the tool migrates both stored workflows (`run_workflows`) **and
frozen run snapshots** (`run_snapshots`). In-flight runs execute against their
snapshot, and the clean-break handler reads canonical keys only, so snapshots
must be normalized too — draining runs is not required. Saves are CAS-aware:
on a version conflict, the tool re-reads, re-normalizes, and retries up to 3
times (`_save_with_retry`).

---

## 4. Runbook

The migration ships in the **same release** as the clean-break handler — no
dual-read code exists, so the code and the data migration must deploy together.

1. **Deploy** the control-plane code. The validation flag
   `NODE_CONFIG_VALIDATION` defaults to `warn`
   (`app/layer2_application/services/node_config_validator.py:42`), so
   non-canonical saves are logged, not rejected, during rollout. Deploy the FE
   lockstep change at the same time (see `node-spec-fe-changes.md`).

2. **Dry-run report** (read-only) from the service root, with the target
   backend configured via the same env the service uses:
   ```
   python -m tools.normalize_node_config
   ```
   Review:
   - `workflow_input_binding_renamed` — how many WORKFLOW nodes will change.
   - `residual validation violations` — workflows that will 400 once `enforce`
     flips; fix or accept per workflow.
   - `UNPARSEABLE input_schema` — must be fixed manually before apply.

3. **Take a DB backup.** There is no automated reverse pass, and code rollback
   alone is *not* sufficient after apply (the old fallback chains are gone).

4. **Apply:**
   ```
   python -m tools.normalize_node_config --apply
   ```
   Normalizes workflows and snapshots with CAS-aware retries. Use
   `--skip-snapshots` only if you deliberately want workflows-only.

5. **Idempotency check:** re-run the dry-run — it must report an empty diff.

6. **Flip `NODE_CONFIG_VALIDATION=enforce`** after one clean warn-mode
   observation window over real save traffic. A later follow-up removes the
   flag entirely (it is a rollout tool, not a living toggle).

7. **Smoke test:** author and run one WORKFLOW sub-workflow with `input_mapping`
   and confirm the child run receives the mapped inputs and completes.

---

## 5. Edge case to verify during the dry-run

The rename in step 3.2 matches a key **literally named `input_binding`** in the
merged FE-view. That correctly catches the dominant legacy shapes — the mapping
stored under the `config.input_binding` key, or under an `input_binding` key on
the entity-level binding field.

It does **not** catch a node that stored the parameter names *directly* on the
entity-level `input_binding` field with no wrapper key, e.g.
`node.input_binding = {"customerId": "{{...}}"}`. There, `merge_parameters`
lifts `customerId` (not a key named `input_binding`) into the merged dict, the
rename branch does nothing, and the re-split routes `customerId` back to the
entity-level binding — where the clean-break handler, which reads only
`cfg.input_mapping`, will not see it.

**Mitigation:** the dry-run report plus a manual inspection of WORKFLOW nodes
confirms whether any such shapes exist. If they do, add a WORKFLOW-specific rule
to lift entity-level binding keys into `input_mapping` before the re-split, and
extend `tests/unit/tools/test_normalize_node_config.py`.

**Likelihood:** low. The frontend never shipped a sub-workflow input-mapping
form — it sends a generic `parameters` object and emits neither `input_binding`
nor `input_mapping` for WORKFLOW nodes. So the only at-risk legacy data is
API-authored or older stored workflows, and the report will quantify it
exactly.

---

## 6. Why not a SQL migration

Node config is JSON inside NCLOB columns (`AIW_WORKFLOWS.NODES`) and snapshot
documents. A Python tool that reads → transforms → CAS-saves is safer than a SQL
`UPDATE`: it validates against the spec, reports violations instead of silently
mangling, folds the row array correctly, and re-homes keys spec-correctly. That
is why migration 042 is an application-level migration rather than a numbered
`.sql` file in the `AIW_SCHEMA_MIGRATIONS` ledger.

---

## 7. Evidence the path works

- Unit: `tests/unit/tools/test_normalize_node_config.py`
  (`test_workflow_input_binding_renamed_and_folded` asserts
  `config.input_mapping == {"a": "{{$n.out}}"}` and that `input_binding` is
  gone), plus idempotency and CAS-retry cases.
- Integration: `tests/integration/test_normalize_node_config_e2e.py` migrates a
  legacy workflow and then runs it to `COMPLETED` through the clean-break
  handlers.
- Validation wiring: `tests/unit/node_spec/test_save_validation_wiring.py` and
  `tests/integration/test_node_config_validation_http.py` exercise the
  `off` / `warn` / `enforce` modes on the save path.

Run the first two to demonstrate the migrate-then-run path:
```
pytest tests/unit/tools/test_normalize_node_config.py \
       tests/integration/test_normalize_node_config_e2e.py
```
