# Node Definition Mapping Guide — HTTP Request Worker

How to define `locked_defaults`, `input_mapping`, and `output_mapping` when creating a business node (NodeDefinition) that wraps the `http-request-worker`.

**Base URL:** `http://localhost:8001`

---

## Table of Contents

1. [How Mapping Works](#how-mapping-works)
2. [HTTP Request Worker Reference](#http-request-worker-reference)
3. [locked_defaults — What to Lock](#locked_defaults--what-to-lock)
4. [input_mapping — User Fields to Worker Fields](#input_mapping--user-fields-to-worker-fields)
5. [output_mapping — Worker Output to Business Fields](#output_mapping--worker-output-to-business-fields)
6. [Expression Syntax](#expression-syntax)
7. [Complete Examples](#complete-examples)
8. [Common Patterns](#common-patterns)

---

## How Mapping Works

When you create a NodeDefinition, you define three things that control how data flows between the **user**, the **worker**, and **downstream nodes**:

```
 USER fills form          input_mapping            WORKER receives
 ─────────────── ──────► transforms fields ──────► flat worker input
   (input_schema)         + locked_defaults

 WORKER returns           output_mapping           DOWNSTREAM nodes
 raw output     ──────► extracts & renames ──────► see clean fields
   (output_schema)                                  via {{$node.key}}
```

### Execution Order at Dispatch Time

```
1. User fills input_schema form        →  { "query": "weather today" }
2. input_binding resolves               →  { "query": "weather today" }
3. input_mapping transforms             →  { "query_params": [{"param_name":"q","param_value":"weather today"}] }
4. locked_defaults override             →  + { "method": "GET", "url": "https://...", "timeout_seconds": 30 }
5. Final payload sent to worker         →  { "method":"GET", "url":"https://...", "query_params":[...], "timeout_seconds":30 }
```

### Execution Order at Completion Time

```
1. Worker returns raw output            →  { "status_code":200, "response_body":{...}, "is_success":true, ... }
2. output_mapping transforms            →  { "result": {...}, "success": true }
3. Stored in run context                →  run.context.variables["my-node-1"] = { "result":{...}, "success":true, ... }
4. Downstream nodes reference           →  {{$my-node-1.result}}
```

---

## HTTP Request Worker Reference

### Worker Input Fields

These are all the fields the `http-request-worker` accepts. Your `locked_defaults` and `input_mapping` must produce values matching these keys.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `method` | string | Yes | HTTP method: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS` |
| `url` | string | Yes | Full URL (must start with `http://` or `https://`) |
| `headers` | array | No | `[{ "header_name": "...", "header_value": "..." }, ...]` |
| `query_params` | array | No | `[{ "param_name": "...", "param_value": "..." }, ...]` |
| `body_type` | string | No | `none`, `raw_json`, `raw_text`, `structured`, `form_urlencoded` |
| `body` | string | No | Raw body content (when `body_type` is `raw_json` or `raw_text`) |
| `body_fields` | array | No | Structured fields (when `body_type` is `structured` or `form_urlencoded`) |
| `timeout_seconds` | number | No | Request timeout, 1-300. Default: 30 |
| `auth_type` | string | No | `none`, `bearer`, `basic`, `api_key` |
| `auth_token` | string | No | Token for `bearer` or `api_key` auth |
| `auth_username` | string | No | Username for `basic` auth |
| `auth_password` | string | No | Password for `basic` auth |
| `api_key_header` | string | No | Header name for `api_key` auth. Default: `X-API-Key` |
| `follow_redirects` | boolean | No | Default: `true` |
| `verify_ssl` | boolean | No | Default: `true` |

### Worker Output Fields

These are the keys available in `output_mapping` via `{{$_output.<key>}}`.

| Key | Type | Always Present | Description |
|-----|------|----------------|-------------|
| `status_code` | number | Yes | HTTP status code (200, 404, 500, ...) |
| `response_headers` | object | Yes | Response headers as key-value dict |
| `response_body` | string or object | Yes | Parsed JSON (if content-type is JSON) or raw text |
| `content_type` | string | Yes | Response Content-Type header value |
| `elapsed_ms` | number | Yes | Request duration in milliseconds |
| `is_success` | boolean | Yes | `true` if status_code is 200-299 |
| `final_url` | string | Yes | Final URL after redirects |

---

## locked_defaults — What to Lock

`locked_defaults` are values **always injected** into the worker payload. Users cannot change them. They override everything.

### Rule: Lock what users should never change

Put these into `locked_defaults`:
- Fixed `method` and `url` (the whole point of the business node)
- Authentication credentials
- Hardcoded headers (Content-Type, Accept)
- Timeouts and SSL settings

Leave these OUT of `locked_defaults` (let users or `input_mapping` provide them):
- Dynamic values like query parameters, body content
- Anything that changes per-execution

### Example: GET endpoint with fixed URL

```json
{
  "locked_defaults": {
    "method": "GET",
    "url": "https://api.example.com/v1/orders",
    "timeout_seconds": 30,
    "auth_type": "bearer",
    "auth_token": "sk-live-xxxxx",
    "follow_redirects": true,
    "verify_ssl": true,
    "headers": [
      { "header_name": "Accept", "header_value": "application/json" }
    ]
  }
}
```

### Example: POST endpoint with fixed body type

```json
{
  "locked_defaults": {
    "method": "POST",
    "url": "https://api.stripe.com/v1/charges",
    "body_type": "raw_json",
    "timeout_seconds": 60,
    "auth_type": "bearer",
    "auth_token": "sk-live-xxxxx",
    "headers": [
      { "header_name": "Content-Type", "header_value": "application/json" }
    ]
  }
}
```

---

## input_mapping — User Fields to Worker Fields

`input_mapping` translates the fields from your `input_schema` into the format the worker expects.

### Context Variables

Inside `input_mapping` expressions, you can reference:

| Variable | Description | Example |
|----------|-------------|---------|
| `$custom` | The user's form input (resolved from `input_schema`) | `{{$custom.query}}` |
| `$input` | The workflow's initial input data | `{{$input.api_key}}` |
| `$<node_id>` | Output of an upstream node | `{{$trigger-1.body.customer_id}}` |

### Pattern 1: Map user field to query_params

**User sees:** A text field labeled "Search Query"
**Worker needs:** `query_params: [{ param_name: "q", param_value: "..." }]`

```json
{
  "input_schema": [
    {
      "key": "query",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Search Query", "required": true },
        "fieldControl": "InputControl",
        "controlProps": { "placeholder": "Enter search term" },
        "rules": [{ "method": "required" }]
      }
    }
  ],
  "input_mapping": {
    "query_params": [
      { "param_name": "q", "param_value": "{{$custom.query}}" }
    ]
  },
  "locked_defaults": {
    "method": "GET",
    "url": "https://www.google.com/search"
  }
}
```

**What the worker receives:**
```json
{
  "method": "GET",
  "url": "https://www.google.com/search",
  "query_params": [{ "param_name": "q", "param_value": "weather today" }]
}
```

### Pattern 2: Map user fields to query_params (multiple)

**User sees:** Two fields — Customer ID and Limit
**Worker needs:** `query_params` with both values

```json
{
  "input_schema": [
    {
      "key": "customer_id",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Customer ID", "required": true },
        "fieldControl": "InputControl",
        "rules": [{ "method": "required" }]
      }
    },
    {
      "key": "limit",
      "outputType": "number",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Max Results" },
        "fieldControl": "InputControl",
        "controlProps": { "type": "number", "placeholder": "50" }
      }
    }
  ],
  "input_mapping": {
    "query_params": [
      { "param_name": "customer_id", "param_value": "{{$custom.customer_id}}" },
      { "param_name": "limit", "param_value": "{{$custom.limit}}" }
    ]
  }
}
```

### Pattern 3: Map user fields to JSON body

**User sees:** Amount and Currency fields
**Worker needs:** `body` as a JSON string

```json
{
  "input_schema": [
    {
      "key": "amount",
      "outputType": "number",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Amount (cents)", "required": true },
        "fieldControl": "InputControl",
        "controlProps": { "type": "number" },
        "rules": [{ "method": "required" }, { "method": "positive" }]
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
            { "id": "eur", "value": "EUR" },
            { "id": "gbp", "value": "GBP" }
          ]
        }
      }
    }
  ],
  "input_mapping": {
    "body": "{\"amount\": {{$custom.amount}}, \"currency\": \"{{$custom.currency}}\"}"
  },
  "locked_defaults": {
    "method": "POST",
    "url": "https://api.stripe.com/v1/charges",
    "body_type": "raw_json"
  }
}
```

### Pattern 4: Map user fields to headers

**User sees:** An API Key field
**Worker needs:** `headers` with Authorization

```json
{
  "input_schema": [
    {
      "key": "api_key",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "API Key", "required": true },
        "fieldControl": "InputControl",
        "controlProps": { "placeholder": "Enter your API key" },
        "rules": [{ "method": "required" }]
      }
    }
  ],
  "input_mapping": {
    "headers": [
      { "header_name": "Authorization", "header_value": "Bearer {{$custom.api_key}}" },
      { "header_name": "Accept", "header_value": "application/json" }
    ]
  }
}
```

### Pattern 5: No input_mapping (pass-through)

If your `input_schema` fields have the **same keys** as the worker input fields, you don't need `input_mapping`. The user's values pass directly through `locked_defaults` merge.

```json
{
  "input_schema": [
    {
      "key": "url",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "URL", "required": true },
        "fieldControl": "InputControl",
        "rules": [{ "method": "required" }]
      }
    }
  ],
  "input_mapping": {},
  "locked_defaults": {
    "method": "GET",
    "timeout_seconds": 30
  }
}
```

User enters `url = "https://example.com"`. Worker receives `{ "method": "GET", "url": "https://example.com", "timeout_seconds": 30 }`.

---

## output_mapping — Worker Output to Business Fields

`output_mapping` extracts and renames fields from the raw worker output so downstream nodes see clean, business-meaningful names.

### Context Variables

Inside `output_mapping` expressions, you can reference:

| Variable | Description |
|----------|-------------|
| `$_output` | The raw worker output dict |

### The raw output from http-request-worker

When the worker completes, it returns:

```json
{
  "status_code": 200,
  "response_headers": { "content-type": "application/json", "x-request-id": "abc" },
  "response_body": { "data": { "items": [...], "total": 42 } },
  "content_type": "application/json",
  "elapsed_ms": 150.5,
  "is_success": true,
  "final_url": "https://api.example.com/v1/orders?customer_id=123"
}
```

### Pattern 1: Extract specific fields

```json
{
  "output_mapping": {
    "orders": "{{$_output.response_body.data.items}}",
    "total": "{{$_output.response_body.data.total}}",
    "success": "{{$_output.is_success}}"
  }
}
```

**Result stored in run context:**
```json
{
  "orders": [...],
  "total": 42,
  "success": true
}
```

**Downstream reference:** `{{$my-node.orders}}`, `{{$my-node.total}}`

### Pattern 2: Pass the entire response body

```json
{
  "output_mapping": {
    "result": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}"
  }
}
```

### Pattern 3: Extract nested JSON response

If the API returns:
```json
{
  "response_body": {
    "user": { "id": 1, "name": "Alice", "email": "alice@example.com" },
    "permissions": ["read", "write"]
  }
}
```

```json
{
  "output_mapping": {
    "user_name": "{{$_output.response_body.user.name}}",
    "user_email": "{{$_output.response_body.user.email}}",
    "permissions": "{{$_output.response_body.permissions}}"
  }
}
```

### Pattern 4: No output_mapping (raw pass-through)

If you omit `output_mapping` (or set it to `{}`), the **entire raw worker output** is stored in the run context. Downstream nodes access the original keys:

```
{{$my-node.status_code}}
{{$my-node.response_body}}
{{$my-node.is_success}}
{{$my-node.response_body.data.items[0].name}}
```

### Pattern 5: Extract array element

```json
{
  "output_mapping": {
    "first_item": "{{$_output.response_body.data.items[0]}}",
    "first_name": "{{$_output.response_body.data.items[0].name}}"
  }
}
```

---

## Expression Syntax

### Basic Format

```
{{$<source>.<path>}}
```

### Path Traversal

| Expression | Resolves To |
|------------|-------------|
| `{{$_output}}` | The entire output dict |
| `{{$_output.status_code}}` | `200` |
| `{{$_output.response_body}}` | The parsed response body (object or string) |
| `{{$_output.response_body.data}}` | Nested field in response body |
| `{{$_output.response_body.items[0]}}` | First element of an array |
| `{{$_output.response_body.items[0].name}}` | Field on first array element |

### String Interpolation

Expressions can be embedded in strings:

```json
{
  "input_mapping": {
    "url": "https://api.example.com/users/{{$custom.user_id}}/orders?status={{$custom.status}}"
  }
}
```

### Important Rules

1. **Source must exist** — `{{$_output.nonexistent_key}}` throws `VariableResolutionError`.
2. **Path must be valid** — `{{$_output.response_body.data[99]}}` fails if index 99 doesn't exist.
3. **Type is preserved** — If `response_body` is a JSON object, the resolved value is a dict, not a string.
4. **String interpolation coerces to string** — `"Status: {{$_output.status_code}}"` produces `"Status: 200"`.
5. **Standalone expression preserves type** — `"{{$_output.status_code}}"` (as the entire value) produces `200` (number), not `"200"`.

---

## Complete Examples

### Example 1: Google Search

```http
POST /node-definitions
```

```json
{
  "tenant_id": "system",
  "name": "Google Search",
  "description": "Search Google with a query string.",
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
        "wrapperProps": { "label": "Search Query", "required": true },
        "fieldControl": "InputControl",
        "controlProps": { "placeholder": "Enter your search term" },
        "rules": [
          { "method": "required" },
          { "method": "min", "value": 1, "message": "Query cannot be empty" }
        ]
      }
    }
  ],
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
  "tags": ["search", "google"]
}
```

**Data flow:**

```
User input:     { "query": "weather today" }
                      │
                      ▼  input_mapping
Worker input:   { "method": "GET",
                  "url": "https://www.google.com/search",
                  "query_params": [{"param_name":"q", "param_value":"weather today"}],
                  "timeout_seconds": 30, ... }
                      │
                      ▼  worker executes GET https://www.google.com/search?q=weather+today
Worker output:  { "status_code": 200,
                  "response_body": "<html>...",
                  "is_success": true,
                  "elapsed_ms": 450, ... }
                      │
                      ▼  output_mapping
Run context:    { "search_result": "<html>...",
                  "status_code": 200,
                  "is_success": true }
                      │
                      ▼  downstream node
                  {{$google-search-1.search_result}}
```

### Example 2: REST API — Get Customer Orders

```json
{
  "tenant_id": "system",
  "name": "Get Customer Orders",
  "description": "Fetch orders for a customer from the internal Orders API.",
  "base_type": "TASK",
  "base_worker_id": "http-request-worker",
  "locked_defaults": {
    "method": "GET",
    "url": "https://api.internal/v1/orders",
    "auth_type": "bearer",
    "auth_token": "internal-service-token-xxx",
    "timeout_seconds": 15,
    "headers": [
      { "header_name": "Accept", "header_value": "application/json" }
    ]
  },
  "input_schema": [
    {
      "key": "customer_id",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Customer ID", "required": true },
        "fieldControl": "InputControl",
        "rules": [{ "method": "required" }]
      }
    },
    {
      "key": "status_filter",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Order Status" },
        "fieldControl": "SelectControl",
        "controlProps": {
          "placeholder": "All statuses",
          "options": [
            { "id": "pending", "value": "pending" },
            { "id": "shipped", "value": "shipped" },
            { "id": "delivered", "value": "delivered" }
          ]
        }
      }
    },
    {
      "key": "limit",
      "outputType": "number",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Max Results" },
        "fieldControl": "InputControl",
        "controlProps": { "type": "number", "placeholder": "50", "value": 50 }
      }
    }
  ],
  "input_mapping": {
    "query_params": [
      { "param_name": "customer_id", "param_value": "{{$custom.customer_id}}" },
      { "param_name": "status", "param_value": "{{$custom.status_filter}}" },
      { "param_name": "limit", "param_value": "{{$custom.limit}}" }
    ]
  },
  "output_mapping": {
    "orders": "{{$_output.response_body.data.items}}",
    "total_count": "{{$_output.response_body.data.total}}",
    "success": "{{$_output.is_success}}"
  },
  "icon": "ShoppingCart",
  "color": "#F59E0B",
  "tags": ["orders", "api"]
}
```

### Example 3: POST — Create a Record

```json
{
  "tenant_id": "system",
  "name": "Create Support Ticket",
  "description": "Create a new support ticket via REST API.",
  "base_type": "TASK",
  "base_worker_id": "http-request-worker",
  "locked_defaults": {
    "method": "POST",
    "url": "https://helpdesk.example.com/api/v2/tickets",
    "body_type": "raw_json",
    "auth_type": "api_key",
    "auth_token": "hd-key-xxxxx",
    "api_key_header": "X-API-Key",
    "timeout_seconds": 30,
    "headers": [
      { "header_name": "Content-Type", "header_value": "application/json" }
    ]
  },
  "input_schema": [
    {
      "key": "subject",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Subject", "required": true },
        "fieldControl": "InputControl",
        "rules": [{ "method": "required" }, { "method": "max", "value": 200 }]
      }
    },
    {
      "key": "description",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Description", "required": true },
        "fieldControl": "TextareaControl",
        "controlProps": { "rowsLength": 5 },
        "rules": [{ "method": "required" }]
      }
    },
    {
      "key": "priority",
      "outputType": "string",
      "fieldConfig": {
        "fieldWrapper": "FormItemWrapper",
        "wrapperProps": { "label": "Priority" },
        "fieldControl": "SelectControl",
        "controlProps": {
          "options": [
            { "id": "low", "value": "Low" },
            { "id": "medium", "value": "Medium" },
            { "id": "high", "value": "High" },
            { "id": "urgent", "value": "Urgent" }
          ]
        }
      }
    }
  ],
  "input_mapping": {
    "body": "{\"ticket\": {\"subject\": \"{{$custom.subject}}\", \"description\": \"{{$custom.description}}\", \"priority\": \"{{$custom.priority}}\"}}"
  },
  "output_mapping": {
    "ticket_id": "{{$_output.response_body.ticket.id}}",
    "ticket_url": "{{$_output.response_body.ticket.url}}",
    "created": "{{$_output.is_success}}"
  },
  "icon": "Ticket",
  "color": "#8B5CF6",
  "tags": ["support", "ticket"]
}
```

### Example 4: Using Upstream Node Data (no user input)

A fully automated node that uses data from a previous node, no `input_schema` needed:

```json
{
  "tenant_id": "system",
  "name": "Send Slack Notification",
  "description": "Posts a message to a Slack webhook. No user input — reads from upstream.",
  "base_type": "TASK",
  "base_worker_id": "http-request-worker",
  "locked_defaults": {
    "method": "POST",
    "url": "https://hooks.slack.com/services/T00/B00/xxxxx",
    "body_type": "raw_json",
    "timeout_seconds": 10,
    "headers": [
      { "header_name": "Content-Type", "header_value": "application/json" }
    ]
  },
  "input_schema": [],
  "input_mapping": {
    "body": "{\"text\": \"Order {{$get-order.order_id}} has been {{$get-order.status}}\"}"
  },
  "output_mapping": {
    "sent": "{{$_output.is_success}}"
  },
  "icon": "MessageSquare",
  "color": "#4A154B",
  "tags": ["slack", "notification"]
}
```

This node reads `order_id` and `status` from an upstream node named `get-order`.

---

## Common Patterns

### Pattern: locked_defaults only (no mapping)

When the user form fields have the **same keys** as worker input fields, skip `input_mapping` entirely. The user's values merge directly with `locked_defaults`.

```json
{
  "input_schema": [
    { "key": "url", ... },
    { "key": "method", ... }
  ],
  "input_mapping": {},
  "locked_defaults": {
    "timeout_seconds": 30,
    "verify_ssl": true
  }
}
```

### Pattern: Dynamic URL with path parameters

```json
{
  "input_mapping": {
    "url": "https://api.example.com/users/{{$custom.user_id}}/orders"
  },
  "locked_defaults": {
    "method": "GET"
  }
}
```

Note: `input_mapping` values override `locked_defaults` for the same key? **No** — `locked_defaults` always win. So don't put `url` in both. Use `input_mapping` for dynamic URLs and leave `url` out of `locked_defaults`.

### Pattern: Conditional output extraction

You can't do conditional logic in `output_mapping`, but you can extract multiple fields and let downstream CONDITION nodes decide:

```json
{
  "output_mapping": {
    "data": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}",
    "is_success": "{{$_output.is_success}}"
  }
}
```

Then use a CONDITION node: `{{$my-api.is_success}} == true`

### Pattern: No output_mapping at all

Omit `output_mapping` to pass the raw worker output as-is. All 7 output fields are available:

```
{{$my-node.status_code}}       → 200
{{$my-node.response_body}}     → { "data": ... }
{{$my-node.is_success}}        → true
{{$my-node.elapsed_ms}}        → 150
{{$my-node.content_type}}      → "application/json"
{{$my-node.response_headers}}  → { "x-request-id": "abc" }
{{$my-node.final_url}}         → "https://api.example.com/v1/orders?..."
```

### Priority Reminder

```
locked_defaults  >  input_mapping  >  user input (input_binding)
```

If `locked_defaults` has `method: "GET"` and `input_mapping` tries to set `method: "POST"`, the worker receives `method: "GET"`. Locked defaults **always win**.
