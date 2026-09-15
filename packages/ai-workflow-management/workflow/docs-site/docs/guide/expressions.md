# Expressions & Variables

Most node config fields accept **expressions** — `{{ ... }}` references that are resolved against the run's accumulated state at execution time. This is how data flows from one node to the next.

A field that supports expressions is marked **`expression: true`** in its [node spec](/nodes/). In the catalog pages you will see this called out per field.

## Syntax

Wrap a reference in double braces:

```
{{$<source>.<path>}}
```

The leading `$` introduces a reference. Everything after it is a dotted path into the run context.

| Reference | Resolves to |
|---|---|
| `{{$<node_id>.output.<field>}}` | A field from an upstream node's output. |
| `{{$input.<field>}}` | A field from the run's `input_data`. |
| `{{$variables.<name>}}` | A run-scoped variable set by a [`SET_VARIABLE`](/nodes/transform#set-variable) node. |
| `{{$metadata.<field>}}` | Run metadata — `run_id`, `workflow_id`, etc. |
| `{{$loop.index}}` | The current iteration index inside a [`LOOP`](/nodes/loops#loop). |
| `{{$loop.item}}` | The current item being iterated. |

::: tip Whole-value vs. embedded
If a field's entire value is a single `{{...}}`, the resolved value keeps its native type (number, object, array). If the expression is embedded in surrounding text — `Hello {{$input.name}}` — the result is string-interpolated.
:::

## Resolution chain

When the engine resolves a reference it walks these layers in order:

1. **Traversal** — upstream node outputs reachable from the current node (`run.context.variables`, keyed by node id).
2. **Metadata** — run-level facts (`run_id`, `workflow_id`, correlation id, …).
3. **Run-scoped variables** — values written by `SET_VARIABLE`.
4. **File-backed values** — large payloads stored out-of-line and fetched on demand.

A node's output is persisted under its `node_id`, which is why `{{$node-1.output.field}}` works from any node downstream of `node-1`.

## Examples

**Pass an upstream HTTP response field into a compute node:**

```
{{$http-1.output.response_body.items}}
```

**Build a prompt from run input** (in a [`PROMPT`](/nodes/transform#prompt) node):

```
Summarize the following ticket for {{$input.customer_name}}:

{{$fetch-ticket.output.body}}
```

**Reference a run-scoped variable** set earlier by `SET_VARIABLE`:

```
{{$variables.userCount}}
```

**Use the loop index** inside a loop body:

```
Processing item #{{$loop.index}}
```

**Map a sub-workflow input** (in a [`WORKFLOW`](/nodes/composition#workflow) node's input mapping):

```
param_name:  orderId
param_value: {{$trigger-1.output.trigger_payload.id}}
```

## Conditions and comparisons

Branching nodes ([`CONDITION`](/nodes/branching#condition), [`SWITCH`](/nodes/branching#switch)) build a comparison from three parts per row:

- **key** — an expression for the left-hand value, e.g. `{{$http-1.output.status_code}}`.
- **operation** — one of the comparison operators below.
- **value** — an expression or literal for the right-hand value, e.g. `200`.

Supported operators include: `=`, `!=`, `>`, `>=`, `<`, `<=`, `contains`, `starts_with`, `is_empty`.

## Design-time autocomplete

While editing a field you can fetch suggestions for what is referenceable at that point in the graph:

```bash
GET /workflows/{workflow_id}/nodes/{node_id}/variable-suggestions?q=stat
```

It returns ranked matches (upstream outputs, variables, metadata) without running the workflow. At runtime, `POST /runs/{run_id}/resolve-variable-paths` resolves concrete `$node.output.field` references against live state.

## Gotchas

- A reference only resolves if the source node is **upstream** of (i.e. reachable before) the node using it. Forward references do not work.
- `{{$variables.x}}` requires a `SET_VARIABLE` (or `user_variables` at run start) to have written `x` first.
- For human-in-the-loop nodes that produce files, reference the file via the node's file output (e.g. `{{$upload-1.output.file_id}}`) rather than trying to inline file contents.
