# Output Mapping & Input Mapping — User Stories & Requirements

## Epic Overview

**Epic:** As a workflow designer, I want to define output mappings with data type conversions on nodes and workflows, so that I can transform raw worker output (e.g., HTTP response bodies) into the exact shape and type downstream nodes or the final workflow output expect.

**Problem:** Currently, output data flows through the engine with minimal transformation. When an HTTP request worker returns an array of objects, but a downstream node only needs an array of strings (e.g., a list of names), the user has no way to configure that conversion. Output schemas exist on `NodeDefinition` and `Workflow`, but there is no user-facing transform pipeline that converts between data types.

**Scope:** This epic covers data mapping at four levels:
1. **Node Definition level** — define the canonical output shape and transforms for a reusable node type
2. **Workflow Node level** — override or customize output mapping for a specific node instance within a workflow
3. **Workflow output level** — define the final workflow output schema and map from internal node outputs
4. **Workflow input level** — transform and reshape raw workflow input before internal nodes consume it

---

## Supported Data Types

| Type | Description | Example |
|------|-------------|---------|
| `string` | Text value | `"hello"` |
| `number` | Integer or float | `42`, `3.14` |
| `boolean` | True/false | `true` |
| `object` | Key-value map | `{"name": "Alice", "age": 30}` |
| `array` | Ordered list | `[1, 2, 3]` |
| `array<string>` | Typed array of strings | `["a", "b", "c"]` |
| `array<number>` | Typed array of numbers | `[1, 2, 3]` |
| `array<object>` | Typed array of objects | `[{"id": 1}, {"id": 2}]` |

---

## Supported Transform Operations

| Operation | Description | Input | Output |
|-----------|-------------|-------|--------|
| `pluck` | Extract a single field from each item in an array | `array<object>` | `array<string\|number>` |
| `flatten` | Flatten nested arrays one level | `array<array>` | `array` |
| `join` | Join array elements into a string with separator | `array<string>` | `string` |
| `split` | Split string into array by separator | `string` | `array<string>` |
| `to_string` | Convert any value to its string representation | `any` | `string` |
| `to_number` | Parse string/boolean to number | `string\|boolean` | `number` |
| `to_boolean` | Convert truthy/falsy values | `any` | `boolean` |
| `json_parse` | Parse JSON string into object/array | `string` | `object\|array` |
| `json_stringify` | Serialize object/array to JSON string | `object\|array` | `string` |
| `pick` | Select specific keys from an object | `object` | `object` (subset) |
| `omit` | Remove specific keys from an object | `object` | `object` (subset) |
| `map_keys` | Rename keys in an object | `object` | `object` |
| `first` | Get the first element of an array | `array` | `any` |
| `last` | Get the last element of an array | `array` | `any` |
| `at` | Get element at a specific index | `array` | `any` |
| `default` | Provide a fallback value if source is null/empty | `any` | `any` |

---

## User Stories

---

### US-1: Define Output Schema on Node Definition

**As a** node definition author,
**I want to** define an output schema with explicit field types on my node definition,
**so that** downstream consumers know the exact shape and types of data this node produces.

#### Acceptance Criteria

- [ ] Each field in `output_schema` supports a `type` property from the supported data types list
- [ ] Each field has a `key` (programmatic name), `label` (display name), and `description`
- [ ] Typed arrays are supported (e.g., `array<string>`, `array<object>`)
- [ ] The schema is validated on save — invalid types are rejected
- [ ] The output schema is returned in `GET /node-definitions/{id}` response
- [ ] Variable suggestion API uses the output schema to offer typed completions

#### Example

**Node Definition: "Fetch Users" (HTTP Request Worker)**

```json
{
  "output_schema": [
    {
      "key": "users",
      "label": "User List",
      "type": "array<object>",
      "description": "List of user objects from API"
    },
    {
      "key": "total_count",
      "label": "Total Count",
      "type": "number",
      "description": "Total number of users returned"
    },
    {
      "key": "is_paginated",
      "label": "Is Paginated",
      "type": "boolean"
    }
  ]
}
```

#### Technical Notes

- **Existing field:** `NodeDefinition.output_schema` in `layer1_domain/entities/node_definition.py` already stores a list of dicts — extend the schema validation to enforce `type` presence
- **Variable suggestions:** `get_variable_suggestions_use_case.py` should use `type` to show typed completions (e.g., show array operations only for array fields)
- **Inheritance:** `node_definition_resolver.py` merge strategy replaces child `output_schema` if non-empty — this remains correct

---

### US-2: Configure Output Mapping with Type Conversion on Node Definition

**As a** node definition author,
**I want to** define output mapping rules that transform raw worker output into the declared output schema, including data type conversions,
**so that** the raw worker response is automatically reshaped before downstream nodes consume it.

#### Acceptance Criteria

- [ ] `output_mapping` supports a `source` expression pointing to a path in the raw worker output (e.g., `_output.response_body.data`)
- [ ] `output_mapping` supports an optional `transforms` array — an ordered pipeline of transform operations applied sequentially
- [ ] Each transform specifies an `operation` (from supported transforms) and operation-specific `params`
- [ ] Type mismatches between transform input and declared output `type` are flagged as warnings on save
- [ ] If no transforms are specified, the source value is passed through as-is (current behavior preserved)
- [ ] The mapping is applied in `complete_task_use_case.py` after receiving the raw worker output

#### Example — HTTP Worker: Array of Objects to Array of Strings

**Scenario:** An HTTP request returns a list of users. The node definition author wants to expose only the user names as a flat string array.

**Raw worker output (`_output`):**
```json
{
  "status_code": 200,
  "response_body": {
    "data": [
      {"id": 1, "name": "Alice", "email": "alice@example.com"},
      {"id": 2, "name": "Bob", "email": "bob@example.com"},
      {"id": 3, "name": "Charlie", "email": "charlie@example.com"}
    ],
    "total": 3
  },
  "is_success": true
}
```

**Output mapping configuration:**
```json
{
  "output_mapping": {
    "user_names": {
      "source": "_output.response_body.data",
      "transforms": [
        {"operation": "pluck", "params": {"field": "name"}}
      ]
    },
    "total_count": {
      "source": "_output.response_body.total"
    },
    "raw_users": {
      "source": "_output.response_body.data"
    }
  }
}
```

**Resulting node output:**
```json
{
  "user_names": ["Alice", "Bob", "Charlie"],
  "total_count": 3,
  "raw_users": [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob", "email": "bob@example.com"},
    {"id": 3, "name": "Charlie", "email": "charlie@example.com"}
  ]
}
```

#### Example — Chained Transforms: Pluck + Join

**Scenario:** Extract email addresses from user list and join them into a comma-separated string.

```json
{
  "output_mapping": {
    "email_list": {
      "source": "_output.response_body.data",
      "transforms": [
        {"operation": "pluck", "params": {"field": "email"}},
        {"operation": "join", "params": {"separator": ", "}}
      ]
    }
  }
}
```

**Result:** `"email_list": "alice@example.com, bob@example.com, charlie@example.com"`

#### Example — Pick Specific Fields from Objects

**Scenario:** From an array of user objects, keep only `id` and `name`.

```json
{
  "output_mapping": {
    "users_slim": {
      "source": "_output.response_body.data",
      "transforms": [
        {"operation": "pick", "params": {"fields": ["id", "name"]}}
      ]
    }
  }
}
```

**Result:**
```json
{
  "users_slim": [
    {"id": 1, "name": "Alice"},
    {"id": 2, "name": "Bob"},
    {"id": 3, "name": "Charlie"}
  ]
}
```

#### Example — JSON String to Object

**Scenario:** HTTP response returns a stringified JSON body. Parse it into an object.

```json
{
  "output_mapping": {
    "parsed_body": {
      "source": "_output.response_body",
      "transforms": [
        {"operation": "json_parse"}
      ]
    }
  }
}
```

#### Technical Notes

- **Current implementation:** `complete_task_use_case.py` (lines 135-149) applies `output_mapping` as simple template rendering with `{"_output": task_output}` context — this needs to be extended to support `source` + `transforms` pipeline
- **Backward compatibility:** If `output_mapping` value is a string (legacy template), treat it as a `source`-only mapping with no transforms
- **Transform engine:** Create a new `OutputTransformPipeline` in `layer1_domain/services/` that takes raw data + mapping config and returns transformed data
- **Existing transforms:** The file-backed resolver (`file_backed_resolver.py`) already implements `pluck`, `join`, `first`, `flat_map` — extract and reuse this logic in the new transform pipeline

---

### US-3: Configure Node Output in Workflow (Per-Instance Override)

**As a** workflow designer,
**I want to** customize the output mapping for a specific node instance within my workflow,
**so that** I can adapt the node's output to the specific needs of this workflow without modifying the shared node definition.

#### Acceptance Criteria

- [ ] `WorkflowNode.output_binding` accepts the same `source` + `transforms` format as `output_mapping`
- [ ] Output binding is applied **after** the node definition's output mapping (layered transformation)
- [ ] If output binding is empty, the node definition's output mapping result is used as-is
- [ ] Output binding can reference the already-mapped output via `_output` context (i.e., it transforms the output_mapping result, not the raw worker output)
- [ ] The workflow PATCH endpoint accepts `output_binding` on node objects
- [ ] Changes to output binding are reflected in the variable suggestion API for downstream nodes

#### Example

**Scenario:** A shared "Fetch Users" node definition produces `user_names: array<string>` and `total_count: number`. In this specific workflow, the designer only needs names as a single comma-separated string.

**Node definition output (after output_mapping):**
```json
{
  "user_names": ["Alice", "Bob", "Charlie"],
  "total_count": 3
}
```

**Workflow node output_binding:**
```json
{
  "output_binding": {
    "names_csv": {
      "source": "_output.user_names",
      "transforms": [
        {"operation": "join", "params": {"separator": ", "}}
      ]
    },
    "count": {
      "source": "_output.total_count"
    }
  }
}
```

**Final node output in this workflow:**
```json
{
  "names_csv": "Alice, Bob, Charlie",
  "count": 3
}
```

#### Technical Notes

- **Existing field:** `WorkflowNode.output_binding` in `layer1_domain/entities/workflow.py` already exists as `dict[str, Any]` — extend processing to support transforms
- **Application order** in `complete_task_use_case.py`: raw output -> `output_mapping` (node def) -> `output_binding` (workflow node) -> store
- **Parameter split:** `output_binding` is already handled separately from the `parameters` split logic (not part of `_CONFIG_KEYS`)

---

### US-4: Define Workflow-Level Output Schema and Mapping

**As a** workflow designer,
**I want to** define the final output schema of my workflow and map it from internal node outputs with type conversions,
**so that** the workflow caller receives a well-defined, predictable response regardless of internal node changes.

#### Acceptance Criteria

- [ ] `Workflow.output_schema` defines the expected fields, types, and labels of the workflow's final output
- [ ] A new `Workflow.output_mapping` field maps each output schema field to a source node output + optional transforms
- [ ] Source expressions reference node outputs by node ID: `$<node_id>.<field_path>`
- [ ] The mapping is applied when the workflow run reaches the terminal node(s) — before writing `run.output_data`
- [ ] If no output mapping is defined, current behavior is preserved (last node output or trigger output)
- [ ] The workflow output schema is returned in `GET /workflows/{id}` and visible to parent workflows (for nested/sub-workflow nodes)
- [ ] Type validation: warn if the source type doesn't match the declared output schema type

#### Example

**Scenario:** A workflow has three nodes: "Fetch Orders" (HTTP), "Calculate Total" (Compute), "Format Report" (Compute). The workflow should output a clean summary.

**Workflow output configuration:**
```json
{
  "output_schema": [
    {"key": "order_ids", "label": "Order IDs", "type": "array<string>"},
    {"key": "grand_total", "label": "Grand Total", "type": "number"},
    {"key": "report_text", "label": "Report", "type": "string"}
  ],
  "output_mapping": {
    "order_ids": {
      "source": "$fetch_orders.raw_orders",
      "transforms": [
        {"operation": "pluck", "params": {"field": "order_id"}},
        {"operation": "pluck", "params": {"field": "toString"}}
      ]
    },
    "grand_total": {
      "source": "$calculate_total.result"
    },
    "report_text": {
      "source": "$format_report.text"
    }
  }
}
```

**Resulting workflow output (`run.output_data`):**
```json
{
  "order_ids": ["ORD-001", "ORD-002", "ORD-003"],
  "grand_total": 1542.50,
  "report_text": "3 orders processed. Total: $1,542.50"
}
```

#### Technical Notes

- **Existing field:** `Workflow.output_schema` in `layer1_domain/entities/workflow.py` already exists — add `output_mapping` field
- **Application point:** `store_output_use_case.py` or the engine's run-completion logic — after all terminal nodes complete, apply workflow-level output mapping before writing `run.output_data`
- **Nested workflows:** When a WORKFLOW-type node completes, its output goes through the child workflow's output mapping, then the parent node's output mapping — both layers apply
- **Variable resolution:** Source expressions follow existing `$node_id.path` convention used by `VariableResolver`

---

### US-5: HTTP Request Worker — Response Body Field Extraction

**As a** workflow designer using the HTTP Request Worker,
**I want to** configure which fields from the HTTP response body are extracted as node outputs,
**so that** downstream nodes receive only the relevant data in the correct types, without dealing with the full HTTP response envelope.

#### Acceptance Criteria

- [ ] The HTTP Request Worker node definition supports output mapping that drills into `response_body`
- [ ] Common extraction patterns are supported:
  - Extract a nested field: `_output.response_body.data.results`
  - Extract and transform: pluck, flatten, type conversion
  - Extract with default: provide fallback if field is missing
- [ ] The user can configure this mapping through the node definition UI (output_schema + output_mapping)
- [ ] The original full response (`status_code`, `headers`, etc.) remains accessible if needed via a "raw" output field

#### Example — REST API Pagination Response

**Raw HTTP response body:**
```json
{
  "status_code": 200,
  "response_body": {
    "results": [
      {"product_id": "P100", "name": "Widget A", "price": 29.99, "tags": ["electronics", "sale"]},
      {"product_id": "P101", "name": "Widget B", "price": 49.99, "tags": ["electronics"]},
      {"product_id": "P102", "name": "Gadget C", "price": 99.99, "tags": ["gadgets", "new"]}
    ],
    "pagination": {
      "page": 1,
      "per_page": 10,
      "total": 3,
      "has_next": false
    }
  },
  "is_success": true,
  "elapsed_ms": 234
}
```

**Output mapping configuration:**
```json
{
  "output_schema": [
    {"key": "products", "label": "Products", "type": "array<object>"},
    {"key": "product_names", "label": "Product Names", "type": "array<string>"},
    {"key": "all_tags", "label": "All Tags", "type": "array<string>"},
    {"key": "total_products", "label": "Total Products", "type": "number"},
    {"key": "has_more", "label": "Has More Pages", "type": "boolean"},
    {"key": "avg_price", "label": "Average Price", "type": "string"}
  ],
  "output_mapping": {
    "products": {
      "source": "_output.response_body.results",
      "transforms": [
        {"operation": "pick", "params": {"fields": ["product_id", "name", "price"]}}
      ]
    },
    "product_names": {
      "source": "_output.response_body.results",
      "transforms": [
        {"operation": "pluck", "params": {"field": "name"}}
      ]
    },
    "all_tags": {
      "source": "_output.response_body.results",
      "transforms": [
        {"operation": "pluck", "params": {"field": "tags"}},
        {"operation": "flatten"}
      ]
    },
    "total_products": {
      "source": "_output.response_body.pagination.total"
    },
    "has_more": {
      "source": "_output.response_body.pagination.has_next"
    },
    "avg_price": {
      "source": "_output.response_body.results",
      "transforms": [
        {"operation": "pluck", "params": {"field": "price"}},
        {"operation": "to_string"}
      ]
    }
  }
}
```

**Resulting node output:**
```json
{
  "products": [
    {"product_id": "P100", "name": "Widget A", "price": 29.99},
    {"product_id": "P101", "name": "Widget B", "price": 49.99},
    {"product_id": "P102", "name": "Gadget C", "price": 99.99}
  ],
  "product_names": ["Widget A", "Widget B", "Gadget C"],
  "all_tags": ["electronics", "sale", "electronics", "gadgets", "new"],
  "total_products": 3,
  "has_more": false,
  "avg_price": "[29.99, 49.99, 99.99]"
}
```

#### Technical Notes

- **Worker output format:** HTTP worker (`worker/http-request-worker/.../execute_task_usecase.py`, lines 325-372) returns fixed fields: `status_code`, `response_headers`, `response_body`, `content_type`, `elapsed_ms`, `is_success`, `final_url`, `resolved_body`
- **No worker changes needed:** All transformation happens at the control-plane level via output mapping — the worker continues returning the full HTTP response
- **File-backed storage:** If the transformed output is a dict/list, `complete_task_use_case.py` stores it as CSV via `data_store_service` — this happens after mapping

---

### US-6: Output Mapping Preview & Validation

**As a** workflow designer,
**I want to** preview the expected output shape after applying my output mapping configuration,
**so that** I can verify the mapping is correct before running the workflow.

#### Acceptance Criteria

- [ ] A new API endpoint accepts an output mapping configuration + sample input data and returns the transformed result
- [ ] Validation checks:
  - Source path exists in sample data
  - Transform operations are valid for the input type (e.g., `pluck` requires array input)
  - Transform chain produces a type compatible with the declared output schema type
- [ ] Validation returns structured warnings/errors (not just pass/fail)
- [ ] The preview endpoint can be called from both node definition and workflow configuration UIs

#### API Contract

**Endpoint:** `POST /api/v1/output-mappings/preview`

**Request:**
```json
{
  "sample_input": {
    "response_body": {
      "data": [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"}
      ]
    },
    "status_code": 200
  },
  "output_mapping": {
    "names": {
      "source": "_output.response_body.data",
      "transforms": [
        {"operation": "pluck", "params": {"field": "name"}}
      ]
    }
  },
  "output_schema": [
    {"key": "names", "type": "array<string>"}
  ]
}
```

**Response:**
```json
{
  "status": "ok",
  "data": {
    "preview": {
      "names": ["Alice", "Bob"]
    },
    "warnings": [],
    "errors": [],
    "type_check": {
      "names": {"expected": "array<string>", "actual": "array<string>", "compatible": true}
    }
  }
}
```

**Error case — invalid source path:**
```json
{
  "status": "ok",
  "data": {
    "preview": {
      "names": null
    },
    "warnings": [],
    "errors": [
      {
        "field": "names",
        "message": "Source path '_output.response_body.users' not found in sample data",
        "code": "SOURCE_PATH_NOT_FOUND"
      }
    ]
  }
}
```

#### Technical Notes

- **New feature:** Create `features/preview_output_mapping/` following the feature auto-discovery convention
- **Reuses:** The `OutputTransformPipeline` from US-2 — same engine, just with sample data instead of live output
- **Controller:** Add to `layer3_adapters/controllers/restful/v1/` as a new controller or extend the node definition controller

---

### US-7: Default Value and Error Handling in Output Mapping

**As a** workflow designer,
**I want to** specify default values and error handling behavior for output mapping fields,
**so that** the workflow doesn't fail when an expected field is missing or a transform encounters unexpected data.

#### Acceptance Criteria

- [ ] Each output mapping field supports an optional `default` value used when:
  - The source path resolves to `null` or `undefined`
  - A transform in the pipeline fails (e.g., `pluck` on a non-array)
- [ ] An optional `on_error` strategy per field: `"use_default"` (default), `"fail"`, `"skip"`
  - `use_default`: Use the default value (or `null` if no default)
  - `fail`: Fail the task with an error describing the mapping failure
  - `skip`: Omit the field from the output entirely
- [ ] Error details are logged for debugging regardless of the strategy chosen

#### Example

```json
{
  "output_mapping": {
    "user_names": {
      "source": "_output.response_body.data",
      "transforms": [
        {"operation": "pluck", "params": {"field": "name"}}
      ],
      "default": [],
      "on_error": "use_default"
    },
    "critical_id": {
      "source": "_output.response_body.id",
      "on_error": "fail"
    },
    "optional_notes": {
      "source": "_output.response_body.notes",
      "default": "",
      "on_error": "use_default"
    }
  }
}
```

---

### US-8: Workflow Input Mapping with Type Conversion

**As a** workflow designer,
**I want to** define an input mapping on my workflow that transforms, reshapes, and type-converts the raw input data before it becomes available as `$input` to internal nodes,
**so that** I can normalize external caller data into the shape my workflow nodes expect, regardless of how the caller structures the payload.

#### Background — Current State

Today, workflow input handling is minimal:
- `Workflow.input_schema` only validates **required field presence** (`SchemaValidator.validate_input` in `layer1_domain/services/schema_validator.py`)
- No type coercion or transformation — raw `input_data` goes directly into `RunContext.variables["input"]`
- The TRIGGER node wraps it as `{"type": "manual", "body": <raw_input>}` without any transformation
- Type coercion only happens per-node at task dispatch time via `VariableResolver.coerce_value()`
- Sub-workflows receive merged parent input via `input_binding` on the WORKFLOW node, but with no transform pipeline

#### Acceptance Criteria

- [ ] A new `input_mapping` field on the `Workflow` entity accepts the same `source` + `transforms` format as output mapping
- [ ] The input mapping is applied **after** schema validation but **before** storing in `RunContext.variables["input"]`
- [ ] Source expressions reference raw input fields via `_input.<path>` (e.g., `_input.payload.users`)
- [ ] All transform operations from the supported transforms list are available (pluck, join, to_string, json_parse, etc.)
- [ ] If no `input_mapping` is defined, current behavior is preserved (raw input stored as-is)
- [ ] The `input_schema` validation applies to the **raw** input (before mapping), ensuring required source fields exist
- [ ] A new optional `mapped_input_schema` describes the shape **after** mapping — this is what `$input` resolves to inside the workflow
- [ ] The workflow create/update endpoints accept `input_mapping` and `mapped_input_schema`
- [ ] Variable suggestion API uses `mapped_input_schema` (if present) instead of `input_schema` for `$input.*` completions
- [ ] Sub-workflow input: when a WORKFLOW node passes input to a child, the child's `input_mapping` is applied to the received data

#### Example 1 — Normalize External API Webhook Payload

**Scenario:** A workflow is triggered by a webhook. The external system sends a nested payload, but the workflow nodes expect a flat structure.

**Raw input from caller:**
```json
{
  "event": "order.created",
  "data": {
    "order": {
      "id": "ORD-789",
      "customer": {
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "tags": ["vip", "returning"]
      },
      "items": [
        {"sku": "SKU-001", "name": "Widget A", "qty": 2, "price": 29.99},
        {"sku": "SKU-002", "name": "Gadget B", "qty": 1, "price": 49.99}
      ],
      "total": 109.97,
      "metadata": "{\"source\": \"mobile_app\", \"version\": \"2.1\"}"
    }
  }
}
```

**Workflow input_schema (validates raw input):**
```json
{
  "input_schema": [
    {"key": "event", "type": "string", "rules": {"isRequired": true}},
    {"key": "data", "type": "object", "rules": {"isRequired": true}}
  ]
}
```

**Workflow input_mapping:**
```json
{
  "input_mapping": {
    "order_id": {
      "source": "_input.data.order.id"
    },
    "customer_name": {
      "source": "_input.data.order.customer.name"
    },
    "customer_email": {
      "source": "_input.data.order.customer.email"
    },
    "customer_tags": {
      "source": "_input.data.order.customer.tags"
    },
    "item_names": {
      "source": "_input.data.order.items",
      "transforms": [
        {"operation": "pluck", "params": {"field": "name"}}
      ]
    },
    "item_skus": {
      "source": "_input.data.order.items",
      "transforms": [
        {"operation": "pluck", "params": {"field": "sku"}},
        {"operation": "join", "params": {"separator": ","}}
      ]
    },
    "total_amount": {
      "source": "_input.data.order.total"
    },
    "parsed_metadata": {
      "source": "_input.data.order.metadata",
      "transforms": [
        {"operation": "json_parse"}
      ]
    }
  }
}
```

**Mapped input_schema (shape of `$input` inside the workflow):**
```json
{
  "mapped_input_schema": [
    {"key": "order_id", "type": "string", "label": "Order ID"},
    {"key": "customer_name", "type": "string", "label": "Customer Name"},
    {"key": "customer_email", "type": "string", "label": "Customer Email"},
    {"key": "customer_tags", "type": "array<string>", "label": "Customer Tags"},
    {"key": "item_names", "type": "array<string>", "label": "Item Names"},
    {"key": "item_skus", "type": "string", "label": "Item SKUs (CSV)"},
    {"key": "total_amount", "type": "number", "label": "Total Amount"},
    {"key": "parsed_metadata", "type": "object", "label": "Parsed Metadata"}
  ]
}
```

**Resulting `$input` available to workflow nodes:**
```json
{
  "order_id": "ORD-789",
  "customer_name": "Alice Johnson",
  "customer_email": "alice@example.com",
  "customer_tags": ["vip", "returning"],
  "item_names": ["Widget A", "Gadget B"],
  "item_skus": "SKU-001,SKU-002",
  "total_amount": 109.97,
  "parsed_metadata": {"source": "mobile_app", "version": "2.1"}
}
```

**Downstream node usage:** `{{$input.customer_name}}` resolves to `"Alice Johnson"`, `{{$input.item_names[0]}}` resolves to `"Widget A"`

#### Example 2 — Type Conversion for Numeric Strings

**Scenario:** An external system sends all values as strings. The workflow needs proper types.

**Raw input:**
```json
{
  "user_id": "12345",
  "amount": "99.50",
  "is_premium": "true",
  "tags": "electronics,sale,new"
}
```

**Input mapping:**
```json
{
  "input_mapping": {
    "user_id": {
      "source": "_input.user_id",
      "transforms": [{"operation": "to_number"}]
    },
    "amount": {
      "source": "_input.amount",
      "transforms": [{"operation": "to_number"}]
    },
    "is_premium": {
      "source": "_input.is_premium",
      "transforms": [{"operation": "to_boolean"}]
    },
    "tags": {
      "source": "_input.tags",
      "transforms": [{"operation": "split", "params": {"separator": ","}}]
    }
  }
}
```

**Resulting `$input`:**
```json
{
  "user_id": 12345,
  "amount": 99.50,
  "is_premium": true,
  "tags": ["electronics", "sale", "new"]
}
```

#### Example 3 — Sub-Workflow Input Transformation

**Scenario:** A parent workflow passes an array of order objects to a child "Process Orders" workflow. The child only needs order IDs and amounts.

**Parent WORKFLOW node config (input_binding):**
```json
{
  "input_binding": {
    "orders": "{{$fetch_orders.raw_orders}}"
  }
}
```

**Child workflow receives:**
```json
{
  "orders": [
    {"order_id": "ORD-1", "customer": "Alice", "amount": 50.00, "items": [...]},
    {"order_id": "ORD-2", "customer": "Bob", "amount": 75.00, "items": [...]}
  ]
}
```

**Child workflow input_mapping:**
```json
{
  "input_mapping": {
    "order_ids": {
      "source": "_input.orders",
      "transforms": [
        {"operation": "pluck", "params": {"field": "order_id"}}
      ]
    },
    "total_amount": {
      "source": "_input.orders",
      "transforms": [
        {"operation": "pluck", "params": {"field": "amount"}}
      ]
    },
    "order_count": {
      "source": "_input.orders",
      "transforms": [
        {"operation": "to_string"}
      ]
    }
  }
}
```

**Resulting `$input` inside child workflow:**
```json
{
  "order_ids": ["ORD-1", "ORD-2"],
  "total_amount": [50.00, 75.00],
  "order_count": "2"
}
```

#### Default Values and Error Handling

Input mapping fields support the same `default` and `on_error` options as output mapping (see US-7):

```json
{
  "input_mapping": {
    "user_name": {
      "source": "_input.data.user.name",
      "default": "Unknown",
      "on_error": "use_default"
    },
    "critical_token": {
      "source": "_input.auth.token",
      "on_error": "fail"
    }
  }
}
```

#### API Contract Changes

**`POST /api/v1/workflows` and `PATCH /api/v1/workflows/{id}`** — add fields:

```json
{
  "input_schema": [...],
  "input_mapping": {
    "<target_field>": {
      "source": "_input.<path>",
      "transforms": [...],
      "default": "<fallback>",
      "on_error": "use_default | fail | skip"
    }
  },
  "mapped_input_schema": [
    {"key": "<target_field>", "type": "<type>", "label": "<label>"}
  ]
}
```

**`POST /api/v1/workflows/{id}/runs` (start_run)** — no API change. Raw input is still sent by the caller. The mapping is applied server-side.

#### Technical Notes

- **Workflow entity:** Add `input_mapping: dict[str, Any]` and `mapped_input_schema: list[dict[str, Any]]` to `Workflow` in `layer1_domain/entities/workflow.py`
- **Application point:** `start_run_use_case.py` (lines 102-125) — after `SchemaValidator.validate_input()` and before storing in `RunContext.variables["input"]`, apply the input mapping pipeline
- **Reuses `OutputTransformPipeline`:** The same transform engine from US-2 applies here — `source` + `transforms` is a universal pattern. Consider naming it `DataTransformPipeline` instead
- **TRIGGER node impact:** The trigger handler wraps `run.input_data` — after input mapping, `run.input_data` should contain the **mapped** data so the trigger node also reflects the mapped shape
- **Variable suggestions:** `get_variable_suggestions_use_case.py` should prefer `mapped_input_schema` over `input_schema` when building `$input.*` completions
- **Sub-workflow:** In `orchestration_engine.py` (lines 1262-1280), after merging parent input into `child_input`, call the child workflow's input mapping before storing in the child `RunContext`
- **Schema validator:** Validation runs on raw input (before mapping). The `mapped_input_schema` is informational for the UI and variable suggestions — no runtime validation against it

---

## Data Type Conversion Matrix

Shows which type-to-type conversions are supported:

| From \ To | `string` | `number` | `boolean` | `object` | `array` | `array<string>` | `array<number>` |
|-----------|----------|----------|-----------|----------|---------|-----------------|-----------------|
| `string` | -- | `to_number` | `to_boolean` | `json_parse` | `split` | `split` | `split` + `to_number` |
| `number` | `to_string` | -- | `to_boolean` | -- | -- | -- | -- |
| `boolean` | `to_string` | `to_number` | -- | -- | -- | -- | -- |
| `object` | `json_stringify` | -- | -- | `pick`/`omit`/`map_keys` | -- | -- | -- |
| `array` | `join` | -- | -- | -- | `flatten` | `pluck` + `to_string` | `pluck` + `to_number` |
| `array<object>` | `join` | -- | -- | `first`/`last`/`at` | `pluck`/`pick` | `pluck` | `pluck` |
| `array<string>` | `join` | -- | -- | -- | -- | -- | `to_number` (each) |

---

## Glossary

| Term | Definition |
|------|------------|
| **Output Schema** | The declared shape (fields + types) of a node's or workflow's output |
| **Output Mapping** | A configuration that transforms raw output data into the declared output schema |
| **Output Binding** | A workflow-node-level override of the output mapping (applied after node definition mapping) |
| **Input Schema** | The declared shape of a workflow's expected raw input (validated before mapping) |
| **Input Mapping** | A configuration that transforms raw workflow input into the shape internal nodes expect |
| **Mapped Input Schema** | The declared shape of `$input` after input mapping is applied — used for variable suggestions |
| **Transform Pipeline** | An ordered list of transform operations applied sequentially to a source value |
| **Source Expression** | A dot-notation path pointing to a value in raw data (e.g., `_output.response_body.data` or `_input.data.order`) |

---

## Key Files Reference

| Component | Path |
|-----------|------|
| Node Definition entity | `layer1_domain/entities/node_definition.py` |
| Workflow / WorkflowNode entity | `layer1_domain/entities/workflow.py` |
| Complete Task use case (applies mapping) | `layer2_application/features/complete_task/use_cases/complete_task_use_case.py` |
| Variable Resolver | `layer1_domain/services/variable_resolver.py` |
| File-backed Resolver (existing transforms) | `layer1_domain/services/file_backed_resolver.py` |
| Node Definition Resolver (inheritance) | `layer1_domain/services/node_definition_resolver.py` |
| Get Node Output use case | `layer2_application/features/get_node_output/use_cases/get_node_output_use_case.py` |
| Store Output use case | `layer2_application/features/store_output/use_cases/store_output_use_case.py` |
| HTTP Worker execute | `worker/http-request-worker/.../execute_task_usecase.py` |
| Data Store Service (CSV storage) | `layer1_domain/services/data_store_service.py` |
| Start Run use case (input entry point) | `layer2_application/features/start_run/use_cases/start_run_use_case.py` |
| Schema Validator (input validation) | `layer1_domain/services/schema_validator.py` |
| Trigger Handler (wraps input) | `layer1_domain/services/node_handlers/trigger_handler.py` |
| Workflow Handler (sub-workflow input) | `layer1_domain/services/node_handlers/workflow_handler.py` |
| Orchestration Engine (child run creation) | `layer1_domain/services/orchestration_engine.py` |
| Variable Suggestions use case | `layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py` |
