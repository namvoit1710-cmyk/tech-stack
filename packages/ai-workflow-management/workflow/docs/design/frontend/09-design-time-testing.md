# Design-Time Testing

Endpoints the canvas uses while the user is still authoring a workflow — test-run the whole thing, test a single node with mock inputs, test a node definition in isolation.

---

## 1. Dry-run a workflow

```
POST /workflows/{workflow_id}/dry-run
Content-Type: application/json

{ "input_data": { "customer": "acme" } }   // DryRunWorkflowRequest
```

Executes the full DAG **without persisting a run**. Returns the execution trace (DAG of node inputs/outputs as if it had run). Use for "preview what this workflow would do" before saving.

No run_id, no socket events, no side effects.

---

## 2. Test a single node

```
POST /workflows/{workflow_id}/nodes/{node_id}/test
Content-Type: application/json

{
  "input_data":     { ... },   // direct node input
  "mock_variables": {          // stand-ins for upstream nodes
    "n_fetch": { "rows": [ ... ] }
  }
}
```

Runs the node with the supplied input and mocked upstream context. Returns the node's output. Use for "test this node" buttons in the node inspector.

---

## 3. Full test run (persisted, but flagged)

```
POST /workflows/{workflow_id}/test/full
Content-Type: application/json

{ "input_data": { ... }, "skip_validation": false }
```

Creates a **real** run, flagged `is_test: true`, with its own `test_run_id`. Emits socket events, creates tasks, stores artifacts — everything exactly as a production run. Use this when you want the user to see live execution on the canvas in "test mode".

To clean up afterward:
```
POST /workflows/{workflow_id}/test/reset
```
Drops every `is_test: true` run for this workflow and its artifacts.

---

## 4. Filtering test runs from production lists

```
GET /runs?$filter=is_test eq false
```
or the query param:
```
GET /runs?is_test=false
```

Default is "show both"; your run list UI probably wants to hide test runs by default and offer a toggle.

---

## 5. Test a node definition

```
POST /node-definitions/{node_definition_id}/test
Content-Type: application/json

{ "input_data": { ... } }
```

Runs the reusable node definition (a template with `locked_defaults`, `input_mapping`, etc.) in isolation. Use inside the node-definition editor. See [`10-node-palette-and-definitions.md`](10-node-palette-and-definitions.md) for definitions themselves.

---

## 6. Validate a workflow

```
POST /workflows/{workflow_id}/validate
```

No execution — only structural / schema validation (edges reference real nodes, required fields present, cycles absent, etc.). Call this on save before PUT.

---

## 7. What you get back

The exact response shapes for `dry-run` and `test/full` include the full trace of nodes executed with their inputs/outputs. The canvas overlay should consume this to draw "this is what each node produced during the test" without needing a real run. Shapes are defined inline in `workflow_controller.py` (search for `DryRunWorkflowRequest`, `TestNodeRequest`, `TestFullWorkflowRequest`).

---

## 8. Source of truth

- Controller: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/workflow_controller.py`
- `node-definitions/{id}/test`: `…/node_definition_controller.py`
