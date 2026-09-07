# Rule Difficulty Router — spec (SA-1613 follow-up)

Status: **DESIGN** (not yet built). Owner: dedup/validation. Relates to: SA-1613
(dedup), SA-1614 (template setup / Verify), SA-1617 (the "brain" / RAG compile).

## Problem

Today every deduplication / validation rule is compiled to an **in-process
Polars expression** and evaluated on the DataFrame in
`PolarsValidatorProvider` (`layer4_frameworks/providers/validation/`). The rule
kinds — `required`, `expression`, `unique`, `set_unique`, `reference_lookup` —
are dispatched by `RuleFactory.get_handler(rule.type)`
(`rule_handlers.py`). This is great for rules that are *column-local* or reduce
to *set membership over a materializable key-set*.

It breaks for rules whose truth depends on **data or logic that does not live in
the uploaded file**. Worked example from the operator:

> "A `Name` that belongs to the group **CAR Nội bộ** is a duplicate."

Here "CAR Nội bộ" is a **data group spanning several tables in an external SAP
2025 database deployed elsewhere**. No Polars expression over the uploaded file
can decide membership — the engine has to reach out to SAP, resolve the group,
and only then flag rows. Pure code cannot express this; the user must supply a
**verification workflow**.

## Mechanism: classify at author-time, stay deterministic at runtime

Each rule carries an `engine` discriminator, decided at **Phase-1 "Verify"**
(the same author-time step where the RAG brain already compiles free-text rules
— see SA-1617):

| `rule.engine` | Meaning | Where it runs |
|---|---|---|
| `inline` | Expressible as a Polars expression over the file | `RuleFactory` handler, in-process ($0, as today) |
| `workflow` | Needs external/multi-table/live data (e.g. SAP) | A pre-built **verification workflow** the user supplies |

### The key insight — hard rules compile *down to* `reference_lookup`

We do **not** call SAP (or any workflow) at validation runtime. Instead, the
hard rule is reduced at author-time to the machinery SA-1358 already proved:

```
Phase-1 (author / Verify — runs ONCE, cached on the template):
  verification workflow "Verify: CAR Nội bộ"  (user-authored, a WORKFLOW node)
    → ISourceReader(sap)  reads the group's tables from SAP 2025      # point-1 DI reader
    → build_reference_keyset  → parquet key-set of member "Name"s     # existing use case
    → cache {key-set file_id, rag_kb_id} on the template             # as rules already cache

Phase-2 (validation runtime — deterministic, unchanged engine):
  reference_lookup(columns=["Name"], reference_source=<cached key-set>,
                   reference_format="parquet", violate_when="in_set")
    → one vectorized is_in membership check, O(n+m), $0 LLM, no SAP call
```

This honors the **deterministic-first doctrine** (`canon/standards/
deterministic-first-doctrine.md`): the stochastic / external work happens once at
author-time; the per-run path is a pure key-set membership.

**Escape hatch (live groups):** if the external group genuinely cannot be
cached (must be re-checked every run), add an orchestration branch in
`DataValidationUseCase` (layer2) that dispatches `engine="workflow"` rules to the
control-plane WORKFLOW node, which returns fresh flagged row-ids / key-set that
are merged into the result. This is slower and non-deterministic — used only
when caching is impossible.

## Routing table (how to classify a new rule)

| Rule shape | engine | node / handler |
|---|---|---|
| Predicate on a column in the file ("not empty", a Polars expr) | inline | `required` / `expression` |
| Uniqueness over columns *in the file* | inline | `set_unique` |
| Membership in a *materializable* set (static/finite) | inline | `reference_lookup` (key-set built once) |
| Membership in an *external / multi-table / SAP-live* group | **workflow** | verification workflow → key-set → `reference_lookup` |

## Verify-before-save (Phase-1)

The template-setup form's "Verify" button gates Save:

- `inline` rule → dry-compile the Polars expression + run it against the sample
  headers; surface compile/column errors before Save.
- `workflow` rule → run the referenced verification workflow against a sample;
  confirm it returns a usable key-set/verdict; cache the artifacts. Only on
  success is the template stored (governance `global-rule-sets`, versioned).

## Implementation sketch (phased, non-breaking)

1. **Schema:** add optional `engine: "inline" | "workflow"` and
   `workflow_ref` / `verification` fields to the rule DTO
   (`layer1_domain/entities/validation.py`). Default `inline` → today's behavior
   is unchanged.
2. **Author-time compile (SA-1617):** the RAG/compile step tags each rule with
   its `engine`. For `workflow` rules it records the `workflow_ref` and, at
   Verify, materializes + caches the key-set.
3. **Runtime:** no change to `PolarsValidatorProvider` for the primary path —
   `workflow` rules have already been reduced to a `reference_lookup` whose
   `reference_source` is the cached key-set. (Only the live-group escape hatch
   touches `DataValidationUseCase`.)
4. **SAP source reader:** implement `SapSourceReader(ISourceReader)` under
   `layer4_frameworks/providers/formats/` (reuses the point-1 DI registry) so the
   verification workflow can `download_and_read` a SAP group as a DataFrame.

## What this reuses (nothing new invented)

- `RuleFactory` dispatch — the `inline` path is untouched.
- `build_reference_keyset` + `reference_lookup` — proven on SA-1358 (dangerous
  substances); the hard rule rides the exact same key-set membership.
- WORKFLOW-node composition — the SA-1615 pattern (each sub-step is its own
  sub-workflow) is how the user's verification workflow plugs in.
- Author-time RAG compile + template caching — already how SA-1613 caches
  compiled rules; the key-set caches the same way.
- The point-1 `ISourceReader` registry — SAP becomes just another registered
  source.
