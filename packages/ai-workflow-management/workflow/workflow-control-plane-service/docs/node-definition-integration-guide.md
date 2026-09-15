# Node Definition Integration Guide

How to create business nodes with custom input/output schemas using the NodeDefinition API, and integrate them into workflows.

**Base URL:** `http://localhost:8001`

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [API Endpoints](#api-endpoints)
4. [Input Schema (AutoFormField Format)](#input-schema-autoformfield-format)
5. [Output Schema](#output-schema)
6. [Input Mapping](#input-mapping)
7. [Output Mapping](#output-mapping)
8. [Locked Defaults](#locked-defaults)
9. [Example: Google Search Business Node](#example-google-search-business-node)
10. [Example: Payment Processing with Inheritance](#example-payment-processing-with-inheritance)
11. [Workflow Integration Lifecycle](#workflow-integration-lifecycle)
12. [Expression Reference](#expression-reference)
13. [Troubleshooting](#troubleshooting)

---

## Overview

A **NodeDefinition** (business node) wraps a base worker (e.g. `http-request-worker`) into a reusable, user-friendly node that appears in the workflow palette. It lets you:

- **Hide complexity** — Lock the HTTP method, URL, and auth into `locked_defaults` so users never see them.
- **Define a custom form** — Expose only the fields users need via `input_schema`.
- **Declare outputs** — Define what the node produces via `output_schema` so downstream nodes know what to expect.
- **Transform data** — Use `input_mapping` to translate user fields into worker fields, and `output_mapping` to extract/rename raw worker output.

---

## Architecture

```
                     Workflow Save Time                          Runtime
                     ═══════════════════                        ═══════════

 ┌──────────────┐    NodeDefinitionResolver     ┌──────────────────────┐
 │  NodeDef     │ ─────────────────────────────► │ node.config          │
 │  (DB)        │    merges parent chain         │   ._node_definition  │
 │              │    into workflow snapshot       │     .input_schema    │
 │ parent_id ───┤                                │     .data_mode       │
 │ locked_def.. │                                │   ._node_internals   │
 │ input_schema │                                │     .locked_defaults │
 │ output_schema│                                │     .input_mapping   │
 │ input_mapping│                                │     .output_mapping  │
 │ output_map.. │                                │     .base_worker_id  │
 └──────────────┘                                └──────────┬───────────┘
                                                            │
                                                  ┌─────────▼──────────┐
                                                  │  TaskNodeHandler    │
                                                  │  1. resolve input   │
                                                  │  2. apply mapping   │
                                                  │  3. merge defaults  │
                                                  │  4. dispatch        │
                                                  └─────────┬──────────┘
                                                            │
                                                  ┌─────────▼──────────┐
                                                  │  Worker Executor    │
                                                  │  → Worker runs      │
                                                  │  → Callback         │
                                                  └─────────┬──────────┘
                                                            │
                                                  ┌─────────▼──────────┐
                                                  │  CompleteTaskUseCase│
                                                  │  1. apply output_   │
                                                  │     mapping         │
                                                  │  2. store in run    │
                                                  │     context         │
                                                  └────────────────────┘
```

---

## API Endpoints

| Method   | Path                            | Description                    |
|----------|---------------------------------|--------------------------------|
| `POST`   | `/node-definitions`             | Create a node definition       |
| `GET`    | `/node-definitions?tenant_id=…` | List definitions by tenant     |
| `GET`    | `/node-definitions/{id}`        | Get a single definition        |
| `PATCH`  | `/node-definitions/{id}`        | Update a definition (partial)  |
| `DELETE` | `/node-definitions/{id}`        | Delete (fails if children exist) |
| `GET`    | `/node-palette?tenant_id=…`     | List all nodes for the palette |

---

## Input Schema (AutoFormField Format)

The `input_schema` defines the form that users fill in when configuring the node in the workflow editor. Each field follows the **AutoFormField** format.

### Field Structure

```json
{
  "key": "field_name",
  "outputType": "string",
  "fieldConfig": {
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": {
      "label": "Display Label",
      "required": true
    },
    "fieldControl": "InputControl",
    "controlProps": {
      "placeholder": "Enter value..."
    },
    "rules": [
      { "method": "required" },
      { "method": "min", "value": 1, "message": "Cannot be empty" }
    ]
  }
}
```

### Available Field Controls

| Control                          | Use For                          |
|----------------------------------|----------------------------------|
| `InputControl`                   | Single-line text or number       |
| `TextareaControl`                | Multi-line text, JSON bodies     |
| `SelectControl`                  | Dropdown with fixed options      |
| `SwitchControl`                  | Boolean toggle                   |
| `CheckBoxControl`                | Checkbox                         |
| `ComboBoxControl`                | Editable dropdown                |
| `MultipleComboboxControl`        | Multi-select dropdown            |
| `UploadControl`            | File upload                      |
| `SingleUploadFieldMappingControl`| Upload with field mapping        |
| `MarkdownControl`                | Read-only markdown display       |
| `NodePopupControl`               | Node reference selector          |
| `RadioPopupControl`              | Radio group with popup           |

### Available Wrappers

| Wrapper              | Use For                           |
|----------------------|-----------------------------------|
| `FormItemWrapper`    | Standard form field with label    |
| `TableWrapper`       | Table container for array fields  |
| `TableRowWrapper`    | Row inside a table                |
| `TableCellWrapper`   | Cell inside a table row           |
| `FragmentWrapper`    | No visual wrapper                 |
| `DivWrapper`         | Generic div container             |
| `SequenceWrapper`    | Ordered sequence                  |
| `TabWrapper`         | Tab container                     |
| `TabItemWrapper`     | Single tab                        |

### Output Types

| Type      | JSON Equivalent |
|-----------|-----------------|
| `string`  | String          |
| `number`  | Number          |
| `boolean` | Boolean         |
| `object`  | Object          |
| `array`   | Array           |

### Validation Rules

| Method       | Value     | Description                     |
|--------------|-----------|---------------------------------|
| `required`   | —         | Field must be filled            |
| `min`        | number    | Minimum length/value            |
| `max`        | number    | Maximum length/value            |
| `pattern`    | regex     | Must match regex pattern        |
| `regex`      | regex     | Alias for pattern               |
| `gt`         | number    | Greater than                    |
| `gte`        | number    | Greater than or equal           |
| `lt`         | number    | Less than                       |
| `lte`        | number    | Less than or equal              |
| `positive`   | —         | Must be > 0                     |
| `int`        | —         | Must be integer                 |
| `trim`       | —         | Trim whitespace before validate |
| `includes`   | string    | Must contain substring          |
| `startsWith` | string    | Must start with                 |
| `endsWith`   | string    | Must end with                   |

### Conditional Fields

Show/hide fields based on another field's value:

```json
{
  "key": "body",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "TextareaControl",
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": { "label": "Request Body" },
    "condition": {
      "field_key": "method",
      "operator": "IN",
      "condition_value": ["POST", "PUT", "PATCH"]
    }
  }
}
```

**Operators:** `EQUALS`, `NOT_EQUALS`, `EXISTS`, `NOT_EXISTS`, `GREATER_THAN`, `LESS_THAN`, `GREATER_OR_EQUAL`, `LESS_OR_EQUAL`, `CONTAINS`, `IN`

### Table Array Fields (Key-Value Pairs)

For inputs like HTTP headers or query parameters:

```json
{
  "key": "headers",
  "outputType": "array",
  "fieldConfig": {
    "fieldWrapper": "TableWrapper",
    "wrapperProps": { "label": "Headers" }
  },
  "fields": [
    {
      "key": "0",
      "outputType": "object",
      "fieldConfig": { "fieldWrapper": "TableRowWrapper" },
      "fields": [
        {
          "key": "header_name",
          "outputType": "string",
          "fieldConfig": {
            "fieldControl": "InputControl",
            "fieldWrapper": "TableCellWrapper",
            "wrapperProps": { "label": "Name" },
            "rules": [{ "method": "required" }]
          }
        },
        {
          "key": "header_value",
          "outputType": "string",
          "fieldConfig": {
            "fieldControl": "InputControl",
            "fieldWrapper": "TableCellWrapper",
            "wrapperProps": { "label": "Value" }
          }
        }
      ]
    }
  ]
}
```

---

## Output Schema

The `output_schema` declares the shape of data the node produces. It uses the same AutoFormField format but with `readonly: true` on controls. This is used by:

1. **The frontend** — to display output data and let users reference fields in downstream nodes.
2. **The palette** — to show what the node outputs.

```json
{
  "output_schema": [
    {
      "key": "status_code",
      "outputType": "number",
      "fieldConfig": {
        "fieldControl": "InputControl",
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "HTTP Status Code" },
        "controlProps": { "readonly": true },
        "rules": [{ "method": "required" }]
      }
    },
    {
      "key": "response_body",
      "outputType": "string",
      "fieldConfig": {
        "fieldControl": "TextareaControl",
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Response Body" },
        "controlProps": { "readonly": true }
      }
    },
    {
      "key": "is_success",
      "outputType": "boolean",
      "fieldConfig": {
        "fieldControl": "SwitchControl",
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Is Success" },
        "controlProps": { "readonly": true }
      }
    }
  ]
}
```

---

## Input Mapping

`input_mapping` transforms user-facing field values into the format the worker expects. It runs at **dispatch time**.

**Context available:**
- `$custom` — the resolved user input (from `input_binding` + form values)
- `$input` — the workflow's initial input data
- `$<node_id>` or `$<node_name>` — any upstream node's output

### Example

User fills in `customer_id` and `limit`. The worker expects `query_params` and `headers`:

```json
{
  "input_mapping": {
    "query_params": [
      { "param_name": "customer_id", "param_value": "{{$custom.customer_id}}" },
      { "param_name": "limit",       "param_value": "{{$custom.limit}}" }
    ],
    "headers": [
      { "header_name": "Authorization", "header_value": "Bearer {{$input.api_key}}" }
    ]
  }
}
```

**Priority order at dispatch:**
```
resolved_input  <  input_mapping result  <  locked_defaults
```
Locked defaults always win.

---

## Output Mapping

`output_mapping` extracts and renames fields from the raw worker output. It runs when **the task completes**.

**Context available:**
- `$_output` — the raw worker output dict (e.g. `status_code`, `response_body`, `is_success`)

### Example

The `http-request-worker` returns:
```json
{
  "status_code": 200,
  "response_headers": { "content-type": "application/json" },
  "response_body": { "results": [...] },
  "is_success": true,
  "elapsed_ms": 150
}
```

To expose only what downstream nodes need:

```json
{
  "output_mapping": {
    "search_result": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}",
    "is_success": "{{$_output.is_success}}"
  }
}
```

After mapping, the run context stores:
```json
{
  "search_result": { "results": [...] },
  "status_code": 200,
  "is_success": true
}
```

Downstream nodes reference these via `{{$google-search-1.search_result}}`.

---

## Locked Defaults

`locked_defaults` are config values injected at dispatch time that **cannot be overridden** by users or input_mapping. Use them for:

- Fixed HTTP methods and URLs
- Authentication credentials
- Timeouts and retry settings
- Any value users should not change

```json
{
  "locked_defaults": {
    "method": "GET",
    "url": "https://www.google.com/search",
    "timeout_seconds": 30,
    "auth_type": "none",
    "follow_redirects": true,
    "verify_ssl": true
  }
}
```

---

## Example: Google Search Business Node

### Step 1 — Create the Node Definition

```http
POST /node-definitions
Content-Type: application/json
```

```json
{
  "tenant_id": "system",
  "name": "Google Search",
  "description": "Performs a Google web search using the provided query string.",
  "base_type": "TASK",
  "base_worker_id": "http-request-worker",
  "locked_defaults": {
    "method": "GET",
    "url": "https://www.google.com/search",
    "timeout_seconds": 30,
    "auth_type": "none",
    "follow_redirects": true,
    "verify_ssl": true
  },
  "input_schema": [
    {
      "key": "query",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": {
          "label": "Search Query",
          "required": true
        },
        "fieldControl": "InputControl",
        "controlProps": {
          "placeholder": "Enter your search term"
        },
        "rules": [
          { "method": "required" },
          { "method": "min", "value": 1, "message": "Query cannot be empty" },
          { "method": "max", "value": 2048, "message": "Query too long" }
        ]
      }
    }
  ],
  "output_schema": [],
  "input_mapping": {
    "query_params": [
      { "param_name": "q", "param_value": "{{$custom.query}}" }
    ]
  },
  "output_mapping": {
    "search_result": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}",
    "is_success": "{{$_output.is_success}}"
  },
  "icon": "Search",
  "color": "#4285F4",
  "tags": ["search", "google", "http"]
}
```

### Step 2 — Create a Workflow Using It

```http
POST /workflows
Content-Type: application/json
```

```json
{
  "name": "Google Search Workflow",
  "description": "User enters a search query and performs a Google web search.",
  "nodes": [
    {
      "id": "input-1",
      "name": "User Input",
      "node_type": "INPUT",
      "config": {
        "input_schema": [
          {
            "key": "search_term",
            "outputType": "string",
            "fieldConfig": {
              "fieldWrapper": "FormItemWrapper",
              "wrapperProps": { "label": "Search Term", "required": true },
              "fieldControl": "InputControl",
              "controlProps": { "placeholder": "What do you want to search?" },
              "rules": [{ "method": "required" }]
            }
          }
        ]
      },
      "x": 100,
      "y": 100
    },
    {
      "id": "google-search-1",
      "name": "Google Search",
      "node_type": "TASK",
      "node_definition_id": "<NODE_DEFINITION_ID>",
      "input_binding": {
        "query": "{{$input-1.search_term}}"
      },
      "x": 100,
      "y": 250
    }
  ],
  "edges": [
    {
      "source": "input-1",
      "source_port": "default",
      "target": "google-search-1",
      "target_port": "in_default"
    }
  ]
}
```

### Step 3 — What Happens at Save Time

The control plane resolves the node definition and embeds a snapshot:

```json
{
  "id": "google-search-1",
  "worker_type": "http-request-worker",
  "config": {
    "_node_definition": {
      "input_schema": [ /* the query field */ ],
      "data_mode": "json",
      "edit_mode": "none"
    },
    "_node_internals": {
      "locked_defaults": {
        "method": "GET",
        "url": "https://www.google.com/search",
        "timeout_seconds": 30,
        "auth_type": "none",
        "follow_redirects": true,
        "verify_ssl": true
      },
      "input_mapping": {
        "query_params": [
          { "param_name": "q", "param_value": "{{$custom.query}}" }
        ]
      },
      "output_mapping": {
        "search_result": "{{$_output.response_body}}",
        "status_code": "{{$_output.status_code}}",
        "is_success": "{{$_output.is_success}}"
      },
      "base_worker_id": "http-request-worker"
    }
  }
}
```

### Step 4 — What Happens at Runtime

1. **INPUT node** pauses the workflow. User submits `{ "search_term": "weather today" }`.
2. **Task handler** resolves:
   - `input_binding`: `query = "weather today"` (from `{{$input-1.search_term}}`)
   - `input_mapping`: `query_params = [{"param_name": "q", "param_value": "weather today"}]`
   - Merges locked_defaults: `method=GET`, `url=https://www.google.com/search`, etc.
3. **Worker receives** the full payload and makes `GET https://www.google.com/search?q=weather+today`.
4. **Worker returns**: `{ "status_code": 200, "response_body": "<html>...", "is_success": true, ... }`
5. **output_mapping** transforms to: `{ "search_result": "<html>...", "status_code": 200, "is_success": true }`
6. **Run context** stores the result as `google-search-1`, accessible by downstream nodes via `{{$google-search-1.search_result}}`.

---

## Example: Payment Processing with Inheritance

### Parent: Base Payment Node

```json
{
  "tenant_id": "acme",
  "name": "Base Payment",
  "description": "Base config for all payment nodes",
  "base_type": "TASK",
  "base_worker_id": "http-request-worker",
  "locked_defaults": {
    "method": "POST",
    "headers": [
      { "header_name": "Content-Type", "header_value": "application/json" },
      { "header_name": "Authorization", "header_value": "Bearer {{$input.payment_api_key}}" }
    ],
    "timeout_seconds": 60
  },
  "output_mapping": {
    "transaction_id": "{{$_output.response_body.id}}",
    "status": "{{$_output.response_body.status}}"
  },
  "icon": "CreditCard",
  "color": "#10B981"
}
```

### Child: Charge Payment

```json
{
  "tenant_id": "acme",
  "name": "Charge Payment",
  "parent_definition_id": "<PARENT_ID>",
  "locked_defaults": {
    "url": "https://api.stripe.com/v1/charges"
  },
  "input_schema": [
    {
      "key": "amount",
      "outputType": "number",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Amount (cents)", "required": true },
        "fieldControl": "InputControl",
        "rules": [
          { "method": "required" },
          { "method": "positive" },
          { "method": "int" }
        ]
      }
    },
    {
      "key": "currency",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Currency" },
        "fieldControl": "SelectControl",
        "controlProps": {
          "options": [
            { "id": "usd", "value": "USD" },
            { "id": "eur", "value": "EUR" }
          ]
        }
      }
    }
  ],
  "input_mapping": {
    "body": "{\"amount\": {{$custom.amount}}, \"currency\": \"{{$custom.currency}}\"}"
  }
}
```

### Resolved Result (at workflow save time)

The child inherits from parent and overrides:

| Field | Source | Value |
|-------|--------|-------|
| `base_worker_id` | parent | `http-request-worker` |
| `locked_defaults.method` | parent | `POST` |
| `locked_defaults.url` | child | `https://api.stripe.com/v1/charges` |
| `locked_defaults.headers` | parent | `[Authorization, Content-Type]` |
| `locked_defaults.timeout_seconds` | parent | `60` |
| `input_schema` | child | `[amount, currency]` |
| `input_mapping` | child | `{body: ...}` |
| `output_mapping` | parent | `{transaction_id, status}` |
| `icon` | parent | `CreditCard` |
| `color` | parent | `#10B981` |

**Merge strategy:**
- `locked_defaults` — shallow merge, child keys override parent keys
- `input_schema` — child replaces entirely if non-empty
- `input_mapping` / `output_mapping` — shallow merge, child keys override
- `icon`, `color` — child wins if non-empty
- `tags` — union (deduplicated)

---

## Workflow Integration Lifecycle

### 1. Palette Discovery

```
GET /node-palette?tenant_id=system
```

Returns business nodes under the `"business"` category:

```json
{
  "categories": {
    "control_flow": [ /* INPUT, CONDITION, LOOP, ... */ ],
    "worker": [ /* http-request-worker, wait-worker, ... */ ],
    "business": [
      {
        "id": "business:b6edefb4-...",
        "node_definition_id": "b6edefb4-...",
        "name": "Google Search",
        "node_type": "TASK",
        "category": "business",
        "input_schema": [ /* query field */ ],
        "output_schema": [],
        "icon": "Search",
        "color": "#4285F4",
        "tags": ["search", "google", "http"]
      }
    ]
  }
}
```

### 2. Workflow Save (Enrichment)

When a workflow is created or updated, for each node with `node_definition_id`:

1. **Resolve** the inheritance chain via `NodeDefinitionResolver`.
2. **Embed** into `node.config._node_definition` (frontend-facing) and `node.config._node_internals` (runtime-facing).
3. **Set** `node.worker_type` from `base_worker_id` if not already set.

### 3. Task Dispatch

The `TaskNodeHandler._apply_node_definition()` method:

1. Gets `_node_internals` from `node.config`.
2. Resolves `input_mapping` expressions against `{...run_context, "custom": user_input}`.
3. Merges: `user_input + mapped_values + locked_defaults` (locked defaults win).
4. Dispatches to the worker with the final payload.

### 4. Task Completion

The `CompleteTaskUseCase`:

1. Receives raw worker output in `output_metadata`.
2. Applies `output_mapping` against `{"_output": raw_worker_output}`.
3. Stores the mapped result in `run.context.variables[node_id]`.
4. Downstream nodes can reference it via `{{$<node_id>.<key>}}`.

---

## Expression Reference

### Syntax

```
{{$<source>.<path>}}
```

- `$input` — workflow input data
- `$<node_id>` — a node's output by ID (e.g. `$google-search-1`)
- `$<node_name>` — a node's output by name (e.g. `$Google Search`)
- `$custom` — user input in input_mapping context
- `$_output` — raw worker output in output_mapping context
- `$loop` — current loop iteration context

### Path Traversal

```
{{$node1.data}}           →  node1_output["data"]
{{$node1.data[0]}}        →  node1_output["data"][0]
{{$node1.data[0].name}}   →  node1_output["data"][0]["name"]
{{$_output.response_body.items[2].id}}  →  deep nested access
```

### String Interpolation

Expressions can be embedded in strings:

```
"Hello {{$input.first_name}} {{$input.last_name}}"
```

---

## Troubleshooting

### output_mapping failed: Key 'X' not found

**Cause:** The raw worker output does not contain the key referenced in the mapping expression. This happens when:

- The worker returned an error or empty output.
- The worker's output structure differs from what the mapping expects.

**Fix:** Check the worker's `output_schema` to see what keys it actually returns. Make sure your `output_mapping` expressions match those keys. For `http-request-worker`, the available keys are: `status_code`, `response_headers`, `response_body`, `content_type`, `elapsed_ms`, `is_success`, `final_url`.

### input_mapping expression fails

**Cause:** The source variable doesn't exist in the run context at dispatch time.

**Fix:** Ensure upstream nodes have completed and their output is in the run context. Use `$custom.<key>` to reference user input from the form, not `$input`.

### Node definition not appearing in palette

**Cause:** The `tenant_id` in the node definition doesn't match the `tenant_id` query parameter.

**Fix:** Check `GET /node-definitions?tenant_id=system` to confirm the definition exists for the right tenant.

### locked_defaults not applied

**Cause:** The node definition was created/updated after the workflow was saved.

**Fix:** Re-save (PATCH) the workflow to trigger re-enrichment. The `_node_internals` snapshot is only updated at workflow save time.

### Child not inheriting parent fields

**Cause:** Merge strategy misunderstanding. `input_schema` uses **replace** (child replaces if non-empty), while `locked_defaults` uses **shallow merge** (child keys override, parent keys preserved).

**Fix:** Leave `input_schema` empty in the child if you want the parent's schema. For `locked_defaults`, only set the keys you want to override.
