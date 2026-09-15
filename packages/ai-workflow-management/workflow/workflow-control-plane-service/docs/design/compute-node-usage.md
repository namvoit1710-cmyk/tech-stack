# COMPUTE Node — Capabilities & Usage Guide

Status: verified 2026-06-12 against `app/layer2_application/services/node_handlers/compute_handler.py`.
Executable contract: `tests/unit/test_compute_handler.py` (85 tests) — update both together.

The COMPUTE node evaluates a single expression inline (no worker dispatch) and
exposes the result as its node output. Evaluation is a **sandboxed AST
whitelist** — not Python `eval`. Anything outside the whitelist below is
rejected.

## Configuration (auto-form)

| Field | Key | Type | Required | Consumed by handler |
|---|---|---|---|---|
| Expression | `config.expression` | textarea (string) | yes | ✅ `compute_handler.py:165` |
| Result Key | `config.result_key` | text (string) | no | ✅ `compute_handler.py:184` |

The auto-form for COMPUTE is **correct**: both fields are consumed, and the
declared output field (`result`) is exactly what the handler produces. No
hidden config keys exist.

## Execution model

1. `config.expression` is run through the **variable resolver** first —
   every `{{$node.output.field}}` template is substituted.
2. The resolved string is evaluated by `_safe_eval` (AST whitelist).
3. The node completes immediately with:

```json
{
  "<node-id>": {
    "output": {
      "result":     <computed value>,
      "expression": "<raw, as authored>",
      "resolved":   "<after template substitution>"
    },
    "metadata": { "type": "compute", "expression": "<raw>", "resolved": "<after substitution>" }
  }
}
```

Downstream nodes read it as `{{$<compute-node>.output.result}}`.
If `result_key` is set, the value is ADDITIONALLY stored as a top-level run
variable under that key (`{{$<result_key>}}`-style access).

`output.resolved` shows the post-substitution expression directly in the
node's output panel — the first place to look when debugging a wrong result
(mirrored in `metadata` for back-compat).

CONDITION and SWITCH expose the same evaluation trace in their outputs:

```json
// CONDITION                                  // SWITCH
{ "result": true,                             { "evaluated_value": "red",
  "branch": "then",                             "matched_case": "redBranch",
  "expression": "{{$a.output.n}} > 5",          "branch": "redBranch",
  "resolved":   "7 > 5" }                       "expression": "{{$in.color}} == red",
                                                "resolved":   "red == red" }
```

## Template substitution rules (important)

| Variable resolves to | Full-match expression (`{{$x}}` alone) | Embedded (`{{$x}} + 1`) |
|---|---|---|
| number | native number | interpolated as literal — works |
| boolean | native bool | interpolated as `True`/`False` — works |
| list / dict | native value | interpolated as `[1, 2, 3]` literal — works |
| **string** | native string → then **fails** as a bare name | interpolated **WITHOUT quotes** |

**Strings must be quoted by the expression author.** A string variable
interpolates raw, so the evaluator sees a bare word and rejects it
(`Name not allowed: hello`):

```text
{{$a.output.name}}                  ✗  soft error
"{{$a.output.name}}" == "hello"     ✓  True
```

JSON-style names `true`, `false`, `null` are accepted (they appear when
variables are interpolated via `json.dumps`).

## Supported expression surface

**Arithmetic** — `+  -  *  /  //  %  **`, unary `+/-`, parentheses, floats.
`/` is true division (`10 / 4` → `2.5`); `//` floors; `**` is power.
`+` and `*` also work on strings and lists (`'ab' + 'cd'`, `'ab' * 2`, `[1,2] + [3]`).

**Comparisons** — `==  !=  <  <=  >  >=`, including chained (`1 < x < 10`).

**Boolean** — `and`, `or`. ⚠️ They reduce via `all()`/`any()` and **return a
bool**, not the last operand like Python: `1 and 2` → `True` (not `2`),
`0 or 3` → `True` (not `3`).
⚠️ **`not` is NOT supported** — use `x == False` or a ternary instead.

**Ternary** — `'bulk' if {{$o.output.qty}} >= 3 else 'single'`.

**Functions (whitelist, simple calls only)** —
`abs round min max sum len int float str`. Nesting is fine:
`max(abs(-3), min(10, 5))`. No other function resolves — `eval`, `open`,
`__import__`, `getattr`, etc. are rejected.

**Literals** — numbers, quoted strings, `True/False/None` (and `true/false/null`),
lists, tuples, dicts.

## Rejected constructs (security sandbox)

Attribute access / method calls (`(1).bit_length()`), subscripts (`x[0]`),
comprehensions, lambdas, f-strings, statements (`;`), unknown names, empty
expressions. Each raises inside the evaluator and surfaces as a soft error
(below). There is no way to reach the filesystem, imports, or arbitrary
builtins from a COMPUTE expression.

## Error behavior

Two distinct failure paths:

* **Template resolution fails** (`{{$missing.path}}`) → the resolution error
  propagates and the TASK FAILS (standard handler input-failure path).
* **Evaluation fails** (bad syntax, `1 / 0`, disallowed construct, unquoted
  string) → **soft error**: the node still COMPLETES on the `success` port
  with

  ```json
  { "result": { "_error": true, "message": "<reason>" } }
  ```

  Downstream CONDITION nodes can route on `{{$compute.output.result._error}}`.

> Note: the handler declares port-routing `success`/`failure` in its comment,
> but the `failure` port is never emitted today — evaluation errors keep the
> `success` port with the `_error` payload. Don't wire a `failure` edge
> expecting evaluation errors to take it.

## Recipes

```text
Order total          {{$order.output.price}} * {{$order.output.qty}}
Rounded              round({{$order.output.price}} * 1.19, 2)
Quorum check         {{$votes.output.yes}} >= len({{$votes.output.members}}) / 2
Tier label           'gold' if {{$user.output.score}} > 90 else 'standard'
Clamp                min(max({{$x.output.v}}, 0), 100)
Safe int parse       int("{{$form.output.age}}")        ← quote string inputs
Negation (no `not`)  {{$flag.output.ok}} == False
```

---

## Appendix — control-flow auto-form audit (2026-06-12)

Cross-reference of palette form schemas (`control_flow_schemas.py`) vs the
config keys each handler actually reads:

| Node | Verdict | Detail |
|---|---|---|
| COMPUTE | ✅ correct | both fields consumed; output carries `result` + evaluation trace (`expression`/`resolved`, added 2026-06-12) |
| CONDITION | ✅ correct | `data[].{key,operations,value}` consumed; 15-operator enum matches `value_comparator` (test-guarded); declared outputs `result`/`branch` are real since 2026-06-12 (plus `expression`/`resolved`) — previously output was `{}` |
| SWITCH | ✅ correct | structured rules + legacy `cases` both handled; declared outputs `evaluated_value`/`matched_case`/`branch` are real since 2026-06-12 (plus `expression`/`resolved`) — previously output was `{}` |
| SET_VARIABLE | ✅ correct | `variables[].{name,type,item_type,value_expression}` all consumed, coercion enums match |
| LOOP_EXIT / LOOP_CONTINUE | ✅ correct | config-free by design, forms expose nothing |
| OUTPUT | ✅ correct | `output_schema[]` consumed; `instruction` is display-only |
| MERGE | ⚠️ partial | `merge_strategy` (wait_all/any/n → all/any/n_of_m, translated at `merge_handler.py:51`) and `wait_count` work. **`timeout_seconds` is a dead field** — no timeout exists. Handler-only keys not in form: `inputs`, `on_branch_failure`, `defaults`. Declared outputs (`branches_received`, `completed`, `timed_out`) are fictional — actual output is the merged predecessor data |
| PARALLEL | ⚠️ partial | **`max_concurrency` and `fail_strategy` are dead fields** — handler reads only `branches[].{condition,nodes}` (canvas-built, not in form). Declared outputs (`branches_total`, …, `all_succeeded`) are fictional — actual output is `{}` |
| TRIGGER | ⚠️ split-consumer | handler reads only `trigger_type`; `schedule_cron` is consumed by the CP scheduler (not the handler), webhook routing goes via `workflow.routing_path`; `event_topic`/`event_filter` have no consumer found |
| WORKFLOW | ⚠️ partial | `workflow_id` + `input_binding` work. **`wait_for_completion` and `timeout_seconds` are dead fields.** Handler-only keys not in form: `on_failure` (fail/skip), `output_binding`, `max_depth` |
| INPUT | ⚠️ partial | `input_schema` works; handler-only keys not in form: `file_id`, `dynamic_table`, `timeout_minutes`, `input_types` |

Dead form fields are silently ignored at runtime (users may believe they
configured a behavior that doesn't exist). Fictional output fields mislead the
variable-suggestion UI. No data-corruption risk in either case.
