# Palette, Node Definitions, Forms

How the canvas renders the drawer of available node types, how custom (business) node definitions are managed, and how the input-panel form is generated from a node's `input_schema` + `controlProps`.

---

## 1. Palette — the canvas drawer

```
GET /node-palette?tenant_id=system
```

Returns the full list of node types available to this tenant:

```jsonc
{
  "nodes": [
    {
      "id":          "HTTP_REQUEST",
      "name":        "HTTP Request",
      "description": "Call an HTTP endpoint",
      "icon":        "http",
      "color":       "#4A90E2",
      "category":    "Integrations",
      "base_type":   "TASK",
      "input_schema": [...], "output_schema": [...], ...
    },
    …
  ]
}
```

Two flavors appear together:

- **Built-in types** — the 13 core node kinds + system workers (HTTP, Agent, Wait, Mapping Data).
- **Business / custom node definitions** — anything the tenant has created via `/node-definitions`.

Group by `category` in the UI. Drag-from-drawer → drop on canvas spawns an instance of that type with `locked_defaults` applied.

### Tenant scoping

- `tenant_id=system` — global catalog (built-in types + any system-authored definitions). Every tenant can see it.
- `tenant_id=<tenant>` — tenant's own custom definitions **merged** with `system`. The server handles the merge; the FE makes one call.

Cache the response per tenant. Invalidate when the tenant creates / updates / deletes a node definition (see §2).

### What each palette entry carries

The entry returned by `/node-palette` is the full effective definition (with inheritance flattened). That means you can render the configuration form from the palette entry directly without a second round-trip — useful for the "preview on hover" tooltip that shows input fields before the user drops.

---

## 2. Node definitions — custom node types

```
POST   /node-definitions              # create
GET    /node-definitions              # list (OData + tenant_id)
GET    /node-definitions/{id}         # fetch one
PATCH  /node-definitions/{id}         # partial update
DELETE /node-definitions/{id}         # delete
POST   /node-definitions/{id}/test    # design-time test
```

### `CreateNodeDefinitionRequest`

```jsonc
{
  "tenant_id":             "…",
  "name":                  "Send approval email",
  "description":           "Sends a templated approval email to the assignee",
  "parent_definition_id":  null,               // inheritance
  "base_type":             "TASK",
  "base_worker_id":        "http-request-worker",
  "locked_defaults":       { "url": "https://…", "method": "POST" },
  "input_schema":          [ { "name": "to",      "type": "string", "controlProps": {...} },
                             { "name": "subject", "type": "string", "controlProps": {...} } ],
  "output_schema":         [...],
  "input_mapping":         { "body.to": "{{$input.to}}", "body.subject": "{{$input.subject}}" },
  "output_mapping":        { "message_id": "$.headers.x-message-id" },
  "icon":                  "envelope",
  "color":                 "#E94B4B",
  "tags":                  ["email", "notification"],
  "ports":                 [ { "id": "out_default", "label": "Sent" } ],
  "data_mode":             "…",
  "edit_mode":             "…"
}
```

### `base_type` + `base_worker_id`

Every definition picks one of two shapes:

- **Wraps a built-in type** (CONDITION / SWITCH / LOOP / MERGE / …): set `base_type`, leave `base_worker_id` null. The definition is a pre-configured instance of that built-in kind. `locked_defaults` fills in the built-in's config (e.g. a fixed condition expression).
- **Wraps a worker** (the common case for TASK): set `base_type: "TASK"` and `base_worker_id: "<worker-type>"`. The definition describes how to call that worker with specific defaults, input mapping, and output mapping.

The palette surfaces both flavors identically — users drag-and-drop a "Send approval email" node without caring that underneath it's `http-request-worker` with locked URL/method.

### `input_mapping` vs `input_schema`

These two fields do different things. Keep them straight:

| Field | What it is | Who reads it |
|---|---|---|
| `input_schema` | Fields the **end user** fills out in the node's form panel. Drives `controlProps` widgets. | FE form renderer. |
| `input_mapping` | How those filled-in scalars are **projected into the worker's native input shape** before dispatch. | Engine at runtime. |

Example: a "Send email" definition might expose `to` and `subject` in `input_schema`, and map `{ "body.to": "{{$input.to}}", "body.subject": "{{$input.subject}}", "body.from": "noreply@…" }` in `input_mapping`. The user never sees `body.from` — it's baked into the mapping. The FE doesn't interpret `input_mapping`; it only renders `input_schema`.

### Inheritance

A definition can inherit from another (`parent_definition_id`): children see the parent's `locked_defaults`, `input_schema`, etc. merged in. The canvas doesn't need to flatten manually — the server returns the effective definition.

When rendering a child in an editor, use `GET /node-definitions/{id}` for the merged view and `GET /node-definitions/{id}?raw=true` (if supported) for just the child's deltas. For most UI needs the merged view is what you want.

### Testing a definition in isolation

Before wiring a new definition into a real workflow, the editor should offer a "Test" button that calls:

```
POST /node-definitions/{id}/test
Content-Type: application/json

{ "input_data": { "to": "test@example.com", "subject": "Hi" } }
```

This executes the definition with `locked_defaults` + `input_mapping` applied, against the real worker, and returns the raw + mapped output. See [`09-design-time-testing.md#5-test-a-node-definition`](09-design-time-testing.md) for the response shape.

### CRUD error handling

| Status | Cause | FE action |
|---|---|---|
| `400` | Validation failed (e.g. `base_type` incompatible with `base_worker_id`, malformed `input_schema`, circular `parent_definition_id`). | Show field-level errors from `error.details`. |
| `403` | Definition belongs to another tenant. | Refresh the list; hide the row. |
| `404` | Definition not found (deleted under you). | Close the editor; invalidate palette cache. |
| `409` on DELETE | Workflow still references this definition. | Show "N workflows still use this — open them" with the list from `error.details.referenced_by[]`. |
| `422` on PATCH | Change breaks existing workflows (e.g. removing a required `input_schema` field). | Prompt "This will break N workflows — continue?"; resubmit with `?force=true`. |

### Delete guard

The DELETE endpoint refuses if a workflow still references the definition. Surface the resulting `error.message` to the user.

---

## 3. Form generation

A node's input panel is built from two inputs the backend never interprets:

1. `input_schema` — list of field descriptors `{ name, type, … }`.
2. `controlProps` (inside each field) — the specific widget to render and its knobs.

The canvas's auto-form library reads these and emits React components. Field types the FE supports typically include: text, number, boolean, select, multi-select, object (nested), array, code-editor, file upload, dynamic-source dropdown, artifact-column picker.

For the exact field list and `controlProps` vocabulary, see `workflow-control-plane-service/docs/schema/node-schema-reference.md` (still accurate under the new engine).

### At runtime

When the user is inside an input-binding slot, show the drag-drop palette ([`03-input-panel-drag-drop.md`](03-input-panel-drag-drop.md)) alongside the widget. On drop, replace the literal value with an expression string (`{{$…}}`).

---

## 4. Dynamic-source dropdowns

Some fields declare `dynamic_source: { worker: "http-request-worker", function: "list_endpoints", params: { ... } }`. Populate these by calling:

```
POST /workers/{worker_type}/functions/{function_name}/invoke
Content-Type: application/json

{ "params": { ... } }
```

Example: a "choose table" dropdown calling `list_tables` on the database worker.

Error handling:
- `503` → worker registry not configured; disable the field or fall back to freeform text.
- `400` with error details → show the worker's error.

Also:
```
GET /workers/{worker_type}/functions
```
returns the full function list for that worker, useful for a "what can this worker do?" admin view.

---

## 5. Expression helper

```
GET /expressions/reference
```

Returns operators, functions, and examples for the expression builder's side-drawer.

```jsonc
{
  "operators":  [ { "symbol": "==", "description": "equal", ... }, ... ],
  "functions":  [ { "name": "lower", "args": ["string"], "returns": "string", ... }, ... ],
  "examples":   [ { "title": "lowercase a value", "expression": "{{lower($node.name)}}", ... }, ... ]
}
```

Render in the "Expression help" panel next to the binding input.

---

## 6. `output_mapping`

For TASK-type definitions, `output_mapping` lets the definition rename or select from the raw worker output before handing it downstream. The object maps `exposed_name → json_path_into_worker_output`. Example:

```jsonc
"output_mapping": {
  "message_id": "$.headers.x-message-id",
  "status":     "$.status"
}
```

Downstream nodes see `{{$this_node.message_id}}`, not the full raw response. The FE doesn't do anything directly with this — it's automatic — but when generating the "what variables can downstream see?" view, remember that `variable-suggestions` already respects this mapping.

---

## 7. `data_mode` and `edit_mode`

Two knobs on a definition that affect runtime UI:

- **`data_mode`** — controls where the node's working copy data lives. `"inline"` (default) stores scalars + descriptors in the run context; `"file_backed"` writes array/object outputs to the file service as artifacts. File-backed is the right choice for anything that emits tabular data (CSV, row sets). This is set by the definition author; the FE just respects what comes back.
- **`edit_mode`** — whether the node is user-editable post-execution. `"readonly"` locks the output panel; `"editable"` allows PATCH /data on the node after it completes. Gate the "Edit" button in the output panel on this flag.

Both fields also appear in the definition editor's advanced section — render them as dropdowns with the allowed values.

---

## 8. Ports

A definition's `ports[]` declares outgoing edges. Built-in types imply ports from `base_type` (e.g. CONDITION implies `then` / `else`); TASK types declare them explicitly.

```jsonc
"ports": [
  { "id": "out_default", "label": "Sent" },
  { "id": "out_error",   "label": "Failed" }
]
```

Two FE uses:

- **Palette preview** — show the ports as output handles on the node chip so users know how many branches will exist after drop.
- **Canvas wiring** — when an edge is drawn out of this node, the port picker uses this list. The port `id` is what goes into the edge's `source_port`.

---

## 9. Canvas-integration checklist

| When the user… | The FE does |
|---|---|
| Opens the canvas drawer | `GET /node-palette` (cache per tenant). |
| Drops a node on canvas | Create a local workflow node from the palette entry; apply `locked_defaults`. |
| Clicks a node's "Configure" | Render the form from `input_schema` + `controlProps`. |
| Focuses a binding field | Show the drag-drop palette from `variable-suggestions`. |
| Hits a dynamic dropdown | `POST /workers/{w}/functions/{f}/invoke`. |
| Opens the expression helper | `GET /expressions/reference` (cache global). |
| Opens a node definition editor | `GET /node-definitions/{id}`. |
| Saves a new definition | `POST /node-definitions`, then invalidate palette cache. |
| Deletes a definition | `DELETE /node-definitions/{id}`; handle the in-use error. |

---

## 10. Caching strategy

| Resource | Cache scope | Invalidate on |
|---|---|---|
| `GET /node-palette?tenant_id=X` | Per tenant, long-lived. | Any `POST`/`PATCH`/`DELETE /node-definitions` call by this tenant. |
| `GET /node-definitions/{id}` | Short-lived (per editor session). | This definition's own `PATCH`. |
| `GET /expressions/reference` | Global, very long-lived. | Server version change. |
| `GET /workers/{w}/functions` | Per worker type, session-long. | Rare — only on worker registry reconfigs. |
| `POST /workers/{w}/functions/{f}/invoke` | Don't cache (params vary). | — |

The palette is the hottest cache. A tenant with 200 custom definitions doesn't want to refetch the full list on every canvas open — gate it on a version counter returned in the palette response (`version: 12`), bump locally when you write a definition, compare on next open.

---

## 11. Source of truth

- Palette controller: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/node_palette_controller.py`
- Node definition controller: `…/node_definition_controller.py`
- Worker function controller: `…/worker_function_controller.py`
- Expression reference: `…/expression_controller.py`
- Schema reference (fields, controlProps catalog): `workflow-control-plane-service/docs/schema/node-schema-reference.md`
- Mapping reference: `workflow-control-plane-service/docs/node-definition-mapping-guide.md`
