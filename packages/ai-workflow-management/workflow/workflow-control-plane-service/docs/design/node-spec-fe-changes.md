# NodeSpec rollout — FE-visible form changes

**Companion to:** `node-spec-single-source.md` (§10) / `node-spec-single-source-tasks.md` (T1.6)
**Date:** 2026-06-12

The spec-driven form renderer reproduces the legacy palette schemas
**byte-for-byte for 12 of 15 node types** (verified by
`tests/unit/test_autoform_golden.py`). Three node types change
intentionally. An FE that renders purely from the served schema needs no
code change for the form itself, but any hardcoded handling of the keys
and values below must ship in lockstep with the backend release that
includes the Phase 6 data migration.

## MERGE

1. **Select option ids for `merge_strategy` change** (labels unchanged):

   | Label (unchanged) | Old id | New id |
   |---|---|---|
   | Wait All | `wait_all` | `all` |
   | Wait Any | `wait_any` | `any` |
   | Wait N | `wait_n` | `n_of_m` |

   The `wait_count` field's visibility condition value changes
   accordingly (`wait_n` → `n_of_m`).

2. **New field `on_branch_failure`** (select: `fail` → "Fail",
   `partial` → "Partial"). The engine always read this key; it now has a
   form field.

Stored workflows are normalized (`wait_all→all` etc., plus
`wait_strategy→merge_strategy`, `n_required→wait_count`) by the
migration; the FE must not send the old vocabulary after this release.

## WORKFLOW (sub-workflow node)

- **Field key `input_binding` → `input_mapping`** (same array-of-rows
  widget, same `param_name`/`param_value` item fields). Renamed because
  the old key collided with the engine-level `WorkflowNode.input_binding`
  field. Stored workflows are migrated; the FE must send `input_mapping`
  after this release.

## PARALLEL

- **Fields `max_concurrency` and `fail_strategy` are removed** from the
  form. They were never read by the engine (dead fields). The node's
  config form is now empty; outputs are unchanged. The fields return
  when the handler actually implements them.

## Everything else

CONDITION, SWITCH, LOOP, TRIGGER, COMPUTE, HUMAN_ACTION,
DATA_EDIT, FILE_UPLOAD, OUTPUT, APPROVAL_FLOW, SET_VARIABLE: rendered
forms are byte-identical to the legacy schemas. (For TRIGGER and
CONDITION/SWITCH the *storage* of some submitted keys moves from
`input_binding` to `config` server-side — invisible to the FE, which
sends and receives the flat `parameters` dict either way.)

## SA-2085 — INPUT node: Form Builder control

The `builtin:input` node's `input_schema` field now renders with
`fieldControl: "FormBuilderControl"` (was `"CodeControl"`). `outputType`
stays `"string"` and the full-span `wrapperProps` are unchanged — the FE
control emits the same JSON-schema string, so this is a drop-in swap of the
editor UI (visual Form Builder in place of the Monaco JSON editor).
