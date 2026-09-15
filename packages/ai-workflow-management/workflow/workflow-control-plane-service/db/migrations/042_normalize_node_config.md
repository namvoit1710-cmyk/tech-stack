# 042 — Normalize node configuration to the NodeSpec layout

**Type:** application-level data migration (no SQL — the workflow/node
documents are JSON columns; the transform logic lives in
`tools/normalize_node_config.py`).
**Design:** `docs/design/node-spec-single-source.md` §9
**FE coordination:** `docs/design/node-spec-fe-changes.md`
**WORKFLOW slice (focused guide):** `docs/design/workflow-input-mapping-migration.md`

## What it does

One-time, idempotent normalization of every stored workflow **and every
run snapshot** (in-flight runs execute against frozen snapshots; the
clean-break handlers read canonical keys only, so snapshots must be
normalized too — draining runs is not required).

| Transform | Detail |
|---|---|
| MERGE | `wait_strategy`→`merge_strategy`; `wait_all/wait_any/wait_n`→`all/any/n_of_m`; `n_required`→`wait_count` |
| LOOP | `items_expression`→`collection` |
| WORKFLOW | config `input_binding`→`input_mapping`; row-array → dict |
| PARALLEL | dead `max_concurrency`/`fail_strategy` dropped (preserved in workflow `metadata._migration_dropped`) |
| CONDITION/SWITCH | `data` re-homed input_binding→config; `expression` dropped when both set |
| TRIGGER | `webhook_path`/`event_filter` re-homed to config |
| INPUT/HUMAN_ACTION/DATA_EDIT | JSON-string `input_schema` parsed once |
| all | `config._node_definition`+`_node_internals` → `definition_snapshot`; merged parameters re-split through the node's spec |
| all | residual `SpecViolation`s **reported**, never auto-fixed |

## Runbook (one release — design §9 rollout order)

1. Merge the NodeSpec Phases 2–5 branch (this migration ships with it;
   no dual-read code exists, so the code and the data migration MUST
   deploy together).
2. **Report pass against prod data** (read-only):

   ```
   python -m tools.normalize_node_config            # dry-run, prints diff summary
   ```

   Review in particular:
   - `SWITCH legacy expression/cases/default nodes` — answers design
     §11.3. Zero occurrences → follow-up PR drops `expression`/`cases`/
     `default` from the SWITCH spec and deletes the legacy branch in
     `switch_handler.py:177-200`. Non-zero → they stay `internal`.
   - `UNPARSEABLE input_schema` — must be fixed manually before apply.
   - `residual validation violations` — workflows that will 400 once
     `enforce` flips; fix or accept per workflow.
3. Deploy the new control-plane code with `NODE_CONFIG_VALIDATION=warn`.
   The FE deploys its lockstep change (`node-spec-fe-changes.md`) at the
   same time.
4. **Apply pass:**

   ```
   python -m tools.normalize_node_config --apply
   ```

   Saves are CAS-aware (re-read + re-normalize + retry on version
   conflict, 3 attempts). Re-run the dry-run afterwards — it must report
   an empty diff (idempotency check).
5. Flip `NODE_CONFIG_VALIDATION=enforce` after one clean warn-mode
   observation window over real save traffic.
6. Follow-up PR: remove the `NODE_CONFIG_VALIDATION` flag (it is a
   rollout tool inside this release, not a living toggle), and resolve
   §11.3 per the report.

## Smoke after apply

Author + run one workflow per re-homed shape: MERGE `n_of_m`,
TRIGGER `schedule`, CONDITION structured rules, WORKFLOW sub-workflow
with `input_mapping`, DATA_EDIT with a `query`.

## Rollback

The transforms are not information-destroying (PARALLEL drops are
preserved in `metadata._migration_dropped`; everything else is renames/
re-homes), but there is no automated reverse pass — roll back by
restoring the pre-apply DB backup taken in step 4 (take one!). Code
rollback alone is NOT sufficient after apply, since the old fallback
chains no longer exist in this release.
