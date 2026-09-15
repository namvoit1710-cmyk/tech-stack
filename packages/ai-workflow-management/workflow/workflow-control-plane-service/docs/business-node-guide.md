# Business Node Guide — Creating & Using NodeDefinitions with Inheritance

This guide walks through creating business nodes (NodeDefinitions), including parent-child inheritance chains, and using them in workflows. All examples include exact JSON payloads for API calls.

**Base URL:** `http://localhost:8001`

---

## Table of Contents

1. [Concepts](#concepts)
2. [API Reference](#api-reference)
3. [Example 1: Standalone Business Node](#example-1-standalone-business-node)
4. [Example 2: Inheritance Chain (Parent + Child)](#example-2-inheritance-chain-parent--child)
5. [Example 3: 3-Level Inheritance Chain](#example-3-3-level-inheritance-chain)
6. [Example 4: Workflow Using Business Nodes](#example-4-workflow-using-business-nodes)
7. [Example 5: Workflow Using Inherited Business Node](#example-5-workflow-using-inherited-business-node)
8. [Example 6: Full End-to-End Integration Test Data](#example-6-full-end-to-end-integration-test-data)
9. [Merge Strategy Reference](#merge-strategy-reference)
10. [Expression Reference](#expression-reference)
11. [Sectioned Input Schemas](#sectioned-input-schemas)
12. [Troubleshooting](#troubleshooting)

---

## Concepts

### What is a Business Node?

A **NodeDefinition** (business node) wraps a base worker with:
- **locked_defaults** — Values injected automatically (hidden from the user)
- **input_schema** — Custom form fields shown in the workflow editor
- **output_schema** — Declares the shape of output for downstream nodes
- **input_mapping** — Transforms user-facing fields → worker-facing fields
- **output_mapping** — Extracts/renames fields from raw worker output

### What is Inheritance?

A child NodeDefinition can set `parent_definition_id` to extend a parent. At workflow save time, the inheritance chain is resolved (merged) into a flat snapshot stored in the workflow. The runtime never sees the chain — it only reads the merged result.

### When Does Resolution Happen?

Resolution happens at **workflow save time** (create or update). The `NodeDefinitionResolver` walks the parent chain and produces a merged result that gets embedded into the workflow node's `config._node_definition` and `config._node_internals`.

---

## API Reference

### Node Definition CRUD

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/node-definitions` | Create a node definition |
| `GET` | `/node-definitions?tenant_id=...` | List definitions by tenant |
| `GET` | `/node-definitions/{id}` | Get a single definition |
| `PATCH` | `/node-definitions/{id}` | Update a definition (partial) |
| `DELETE` | `/node-definitions/{id}` | Delete a definition |

### Workflow CRUD

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workflows` | Create a workflow |
| `PUT` | `/workflows/{id}` | Replace a workflow |
| `PATCH` | `/workflows/{id}` | Patch a workflow |
| `GET` | `/workflows/{id}` | Get a workflow |

### Run

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/runs` | Start a workflow run |

---

## Example 1: Standalone Business Node

Create a simple business node that wraps an HTTP worker to fetch customer orders.

### Step 1: Create the NodeDefinition

```http
POST /node-definitions
Content-Type: application/json
```

```json
{
    "tenant_id": "acme-corp",
    "name": "Get Customer Orders",
    "description": "Fetch orders for a customer from the Orders API. User provides customer_id; method and base URL are locked.",
    "parent_definition_id": null,
    "base_type": "TASK",
    "base_worker_id": "http-request-worker",
    "locked_defaults": {
        "method": "GET",
        "url": "https://api.internal/v1/orders",
        "headers": {
            "Accept": "application/json"
        }
    },
    "input_schema": [
        {
            "key": "customer_id",
            "type": "string",
            "label": "Customer ID",
            "required": true
        },
        {
            "key": "limit",
            "type": "number",
            "label": "Max Results",
            "required": false,
            "default": 50
        }
    ],
    "output_schema": [
        {
            "key": "orders",
            "type": "array",
            "label": "Orders"
        },
        {
            "key": "total_count",
            "type": "number",
            "label": "Total Count"
        }
    ],
    "input_mapping": {
        "query_params": {
            "customer_id": "{{$custom.customer_id}}",
            "limit": "{{$custom.limit}}"
        }
    },
    "output_mapping": {
        "orders": "{{$_output.output.body.items}}",
        "total_count": "{{$_output.output.body.total}}"
    },
    "icon": "ShoppingCart",
    "color": "#3B82F6",
    "tags": ["orders", "crm", "http"],
    "data_mode": "json",
    "edit_mode": "none"
}
```

### Expected Response

```json
{
    "status": "ok",
    "data": {
        "id": "a1b2c3d4-...",
        "tenant_id": "acme-corp",
        "name": "Get Customer Orders",
        "description": "Fetch orders for a customer from the Orders API...",
        "parent_definition_id": null,
        "base_type": "TASK",
        "base_worker_id": "http-request-worker",
        "locked_defaults": {
            "method": "GET",
            "url": "https://api.internal/v1/orders",
            "headers": {"Accept": "application/json"}
        },
        "input_schema": [
            {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
            {"key": "limit", "type": "number", "label": "Max Results", "required": false, "default": 50}
        ],
        "output_schema": [
            {"key": "orders", "type": "array", "label": "Orders"},
            {"key": "total_count", "type": "number", "label": "Total Count"}
        ],
        "input_mapping": {"query_params": {"customer_id": "{{$custom.customer_id}}", "limit": "{{$custom.limit}}"}},
        "output_mapping": {"orders": "{{$_output.output.body.items}}", "total_count": "{{$_output.output.body.total}}"},
        "icon": "ShoppingCart",
        "color": "#3B82F6",
        "tags": ["orders", "crm", "http"],
        "data_mode": "json",
        "edit_mode": "none",
        "created_at": "2026-03-23T...",
        "updated_at": "2026-03-23T...",
        "error": ""
    }
}
```

---

## Example 2: Inheritance Chain (Parent + Child)

### Step 1: Create Parent — "Charge Payment"

```http
POST /node-definitions
Content-Type: application/json
```

```json
{
    "tenant_id": "acme-corp",
    "name": "Charge Payment",
    "description": "Charge a customer via the payment gateway. User provides customer and amount; gateway/currency are locked.",
    "parent_definition_id": null,
    "base_type": "TASK",
    "base_worker_id": "payment-worker",
    "locked_defaults": {
        "gateway": "stripe",
        "currency": "USD"
    },
    "input_schema": [
        {
            "key": "customer_details",
            "type": "section",
            "label": "Customer Details",
            "fields": [
                {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
                {"key": "email", "type": "string", "label": "Email"}
            ]
        },
        {
            "key": "payment_details",
            "type": "section",
            "label": "Payment Details",
            "fields": [
                {"key": "amount", "type": "number", "label": "Amount (USD)", "required": true},
                {"key": "description", "type": "string", "label": "Payment Description"}
            ]
        }
    ],
    "output_schema": [
        {"key": "transaction_id", "type": "string", "label": "Transaction ID"},
        {"key": "charged_amount", "type": "number", "label": "Charged Amount"}
    ],
    "input_mapping": {
        "charge_amount": "{{$custom.amount}}",
        "customer_ref": "{{$custom.customer_id}}"
    },
    "output_mapping": {
        "transaction_id": "{{$_output.output.transaction_id}}",
        "charged_amount": "{{$_output.output.charged_amount}}"
    },
    "icon": "CreditCard",
    "color": "#10B981",
    "tags": ["payment", "finance"],
    "data_mode": "json",
    "edit_mode": "none"
}
```

> Save the returned `id` — e.g. `"nd-charge-payment"`. We'll use it as the parent.

### Step 2: Create Child — "Premium Payment" (extends Charge Payment)

```http
POST /node-definitions
Content-Type: application/json
```

```json
{
    "tenant_id": "acme-corp",
    "name": "Premium Payment",
    "description": "Premium tier payment. Extends Charge Payment with premium defaults and HITL review.",
    "parent_definition_id": "nd-charge-payment",
    "base_type": "TASK",
    "base_worker_id": null,
    "locked_defaults": {
        "payment_tier": "premium",
        "premium_fee_percent": 5
    },
    "input_schema": [
        {
            "key": "customer_details",
            "type": "section",
            "label": "Customer Details",
            "fields": [
                {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
                {"key": "email", "type": "string", "label": "Email"}
            ]
        },
        {
            "key": "payment_details",
            "type": "section",
            "label": "Payment Details",
            "fields": [
                {"key": "amount", "type": "number", "label": "Amount (USD)", "required": true},
                {"key": "description", "type": "string", "label": "Payment Description"}
            ]
        },
        {
            "key": "premium_options",
            "type": "section",
            "label": "Premium Options",
            "fields": [
                {"key": "priority", "type": "string", "label": "Priority", "required": false, "default": "normal"},
                {"key": "expedited", "type": "boolean", "label": "Expedited Processing", "default": false}
            ]
        }
    ],
    "input_mapping": {
        "metadata": {
            "priority": "{{$custom.priority}}",
            "expedited": "{{$custom.expedited}}"
        }
    },
    "output_mapping": {},
    "icon": "Crown",
    "color": "#F59E0B",
    "tags": ["payment", "premium"],
    "data_mode": "json",
    "edit_mode": "before_dispatch"
}
```

**Note:** `base_worker_id` is `null` — it will be inherited from the parent (`payment-worker`).

### What the Resolver Produces (merged result)

When a workflow node references `"nd-premium-payment"`, the enrichment produces:

```json
{
    "_node_definition": {
        "input_schema": [
            {
                "key": "customer_details", "type": "section", "label": "Customer Details",
                "fields": [
                    {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
                    {"key": "email", "type": "string", "label": "Email"}
                ]
            },
            {
                "key": "payment_details", "type": "section", "label": "Payment Details",
                "fields": [
                    {"key": "amount", "type": "number", "label": "Amount (USD)", "required": true},
                    {"key": "description", "type": "string", "label": "Payment Description"}
                ]
            },
            {
                "key": "premium_options", "type": "section", "label": "Premium Options",
                "fields": [
                    {"key": "priority", "type": "string", "label": "Priority", "required": false, "default": "normal"},
                    {"key": "expedited", "type": "boolean", "label": "Expedited Processing", "default": false}
                ]
            }
        ],
        "data_mode": "json",
        "edit_mode": "before_dispatch"
    },
    "_node_internals": {
        "locked_defaults": {
            "gateway": "stripe",
            "currency": "USD",
            "payment_tier": "premium",
            "premium_fee_percent": 5
        },
        "input_mapping": {
            "charge_amount": "{{$custom.amount}}",
            "customer_ref": "{{$custom.customer_id}}",
            "metadata": {
                "priority": "{{$custom.priority}}",
                "expedited": "{{$custom.expedited}}"
            }
        },
        "output_mapping": {
            "transaction_id": "{{$_output.output.transaction_id}}",
            "charged_amount": "{{$_output.output.charged_amount}}"
        },
        "base_worker_id": "payment-worker"
    }
}
```

---

## Example 3: 3-Level Inheritance Chain

```
Root: "HTTP API Call"       → base worker = http-request-worker
  └─ Child: "GET Orders"   → locks method=GET, url=/orders
      └─ Grandchild: "GET VIP Orders" → locks customer_tier=vip, edit_mode=before_dispatch
```

### Step 1: Root — "HTTP API Call"

```json
{
    "tenant_id": "acme-corp",
    "name": "HTTP API Call",
    "description": "Generic HTTP request wrapper",
    "parent_definition_id": null,
    "base_type": "TASK",
    "base_worker_id": "http-request-worker",
    "locked_defaults": {
        "timeout_ms": 30000,
        "retry_count": 3
    },
    "input_schema": [
        {"key": "url", "type": "string", "label": "URL", "required": true},
        {"key": "method", "type": "string", "label": "HTTP Method", "required": true},
        {"key": "body", "type": "object", "label": "Request Body"}
    ],
    "output_schema": [
        {"key": "status_code", "type": "number", "label": "Status Code"},
        {"key": "body", "type": "object", "label": "Response Body"}
    ],
    "input_mapping": {},
    "output_mapping": {
        "status_code": "{{$_output.output.status_code}}",
        "body": "{{$_output.output.body}}"
    },
    "icon": "Globe",
    "color": "#6366F1",
    "tags": ["http", "api"],
    "data_mode": "json",
    "edit_mode": "none"
}
```

### Step 2: Child — "GET Orders" (extends "HTTP API Call")

```json
{
    "tenant_id": "acme-corp",
    "name": "GET Orders",
    "description": "Fetch orders — method and URL are locked",
    "parent_definition_id": "<id-of-http-api-call>",
    "base_type": "TASK",
    "base_worker_id": null,
    "locked_defaults": {
        "method": "GET",
        "url": "https://api.internal/v1/orders"
    },
    "input_schema": [
        {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
        {"key": "limit", "type": "number", "label": "Max Results", "default": 50}
    ],
    "output_schema": [
        {"key": "orders", "type": "array", "label": "Orders"}
    ],
    "input_mapping": {
        "query_params": {
            "customer_id": "{{$custom.customer_id}}",
            "limit": "{{$custom.limit}}"
        }
    },
    "output_mapping": {
        "orders": "{{$_output.body.items}}"
    },
    "icon": "ShoppingCart",
    "color": "#3B82F6",
    "tags": ["orders"],
    "data_mode": "json",
    "edit_mode": "none"
}
```

### Step 3: Grandchild — "GET VIP Orders" (extends "GET Orders")

```json
{
    "tenant_id": "acme-corp",
    "name": "GET VIP Orders",
    "description": "VIP customer orders — requires human review before dispatch",
    "parent_definition_id": "<id-of-get-orders>",
    "base_type": "TASK",
    "base_worker_id": null,
    "locked_defaults": {
        "customer_tier": "vip"
    },
    "input_schema": [],
    "output_schema": [],
    "input_mapping": {},
    "output_mapping": {},
    "icon": "Crown",
    "color": "#F59E0B",
    "tags": ["vip"],
    "data_mode": "json",
    "edit_mode": "before_dispatch"
}
```

### Resolved Merge for "GET VIP Orders"

| Field | Root | Child | Grandchild | **Resolved** |
|-------|------|-------|------------|--------------|
| `base_worker_id` | `http-request-worker` | null | null | **`http-request-worker`** |
| `locked_defaults` | `{timeout_ms, retry_count}` | `{method, url}` | `{customer_tier}` | **`{timeout_ms, retry_count, method, url, customer_tier}`** |
| `input_schema` | `[url, method, body]` | `[customer_id, limit]` | `[]` (empty) | **`[customer_id, limit]`** (last non-empty wins) |
| `output_schema` | `[status_code, body]` | `[orders]` | `[]` (empty) | **`[orders]`** |
| `input_mapping` | `{}` | `{query_params: ...}` | `{}` | **`{query_params: ...}`** |
| `output_mapping` | `{status_code, body}` | `{orders}` | `{}` | **`{status_code, body, orders}`** (shallow merge) |
| `icon` | `Globe` | `ShoppingCart` | `Crown` | **`Crown`** |
| `color` | `#6366F1` | `#3B82F6` | `#F59E0B` | **`#F59E0B`** |
| `tags` | `[http, api]` | `[orders]` | `[vip]` | **`[http, api, orders, vip]`** |
| `data_mode` | `json` | `json` | `json` | **`json`** |
| `edit_mode` | `none` | `none` | `before_dispatch` | **`before_dispatch`** |

---

## Example 4: Workflow Using Business Nodes

Create a workflow with a trigger → business node → end.

### Prerequisites

Use the "Get Customer Orders" definition from Example 1 — save its `id` as `ND_ID`.

### Create Workflow

```http
POST /workflows
Content-Type: application/json
```

```json
{
    "name": "Fetch Orders Workflow",
    "description": "Simple workflow that fetches customer orders",
    "input_schema": [
        {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true}
    ],
    "output_schema": [
        {"key": "orders", "type": "array", "label": "Orders"}
    ],
    "nodes": [
        {
            "id": "trigger-start",
            "name": "Start",
            "type": "TRIGGER",
            "parameters": {
                "trigger_type": "manual"
            },
            "ports": {
                "in": [],
                "out": [{"id": "default", "label": "Start"}]
            },
            "x": 100,
            "y": 200
        },
        {
            "id": "fetch-orders",
            "name": "Fetch Orders",
            "type": "TASK",
            "node_class": "BUSINESS",
            "node_definition_id": "<ND_ID>",
            "parameters": {
                "customer_id": "{{$input.customer_id}}",
                "limit": 100
            },
            "ports": {
                "in": [{"id": "in_default", "label": "Input"}],
                "out": [
                    {"id": "success", "label": "Success"},
                    {"id": "failure", "label": "Failure"}
                ]
            },
            "x": 400,
            "y": 200
        }
    ],
    "edges": [
        {
            "source_node_id": "trigger-start",
            "target_node_id": "fetch-orders",
            "source_port_id": "default",
            "target_port_id": "in_default"
        }
    ],
    "metadata": {},
    "schema_version": "1.0.0"
}
```

### What Happens at Save Time

The enrichment pipeline:
1. Sees `fetch-orders` has `node_definition_id = "<ND_ID>"`
2. Calls `NodeDefinitionResolver.resolve("<ND_ID>", repo)`
3. Since there is no parent, it returns the definition as-is
4. Embeds into `fetch-orders.config`:
   ```json
   {
       "_node_definition": {
           "input_schema": [
               {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
               {"key": "limit", "type": "number", "label": "Max Results", "required": false, "default": 50}
           ],
           "data_mode": "json",
           "edit_mode": "none"
       },
       "_node_internals": {
           "locked_defaults": {
               "method": "GET",
               "url": "https://api.internal/v1/orders",
               "headers": {"Accept": "application/json"}
           },
           "input_mapping": {
               "query_params": {
                   "customer_id": "{{$custom.customer_id}}",
                   "limit": "{{$custom.limit}}"
               }
           },
           "output_mapping": {
               "orders": "{{$_output.output.body.items}}",
               "total_count": "{{$_output.output.body.total}}"
           },
           "base_worker_id": "http-request-worker"
       }
   }
   ```
5. Sets `fetch-orders.worker_id = "http-request-worker"` (auto-filled from definition)

### What Happens at Runtime

```
1. Run starts → trigger fires → "fetch-orders" task created

2. TaskHandler.handle() runs:
   a. Resolve input_binding:
      resolved_input = {customer_id: "C-123", limit: 100}

   b. _apply_node_definition():
      - Reads _node_internals
      - Resolves input_mapping with context {custom: resolved_input}:
        mapped = {query_params: {customer_id: "C-123", limit: 100}}
      - Merges: {...resolved_input, ...mapped, ...locked_defaults}
      - Final task input:
        {
          customer_id: "C-123",
          limit: 100,
          query_params: {customer_id: "C-123", limit: 100},
          method: "GET",
          url: "https://api.internal/v1/orders",
          headers: {Accept: "application/json"}
        }

   c. edit_mode = "none" → dispatches immediately to http-request-worker

3. Worker returns raw output:
   {output: {status_code: 200, body: {items: [...], total: 42}}}

4. CompleteTaskUseCase:
   - Applies output_mapping:
     {orders: [...], total_count: 42}
   - Stores in run.context.variables["fetch-orders"]
   - Also stores under the display name ("Fetch Orders") for expression
     resolution, but this alias is NOT included in the run's output_data

5. Run completes → output_data contains only canonical keys:
   {
     input: {customer_id: "C-123"},
     "trigger-start": {output: {...}, metadata: {...}},
     "fetch-orders": {orders: [...], total_count: 42}
   }
   Note: the "trigger" convenience alias and name aliases are excluded.
```

### Start a Run

```http
POST /runs
Content-Type: application/json
```

```json
{
    "workflow_id": "<WORKFLOW_ID>",
    "input_data": {
        "customer_id": "C-123"
    },
    "tenant_id": "acme-corp"
}
```

---

## Example 5: Workflow Using Inherited Business Node

Uses the "Premium Payment" child definition from Example 2.

### Create Workflow

```http
POST /workflows
Content-Type: application/json
```

```json
{
    "name": "Premium Payment Workflow",
    "description": "Process a premium payment with HITL review",
    "input_schema": [
        {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
        {"key": "customer_email", "type": "string", "label": "Customer Email"},
        {"key": "total_amount", "type": "number", "label": "Total Amount", "required": true}
    ],
    "nodes": [
        {
            "id": "trigger",
            "name": "Start",
            "type": "TRIGGER",
            "parameters": {
                "trigger_type": "manual"
            },
            "ports": {
                "in": [],
                "out": [{"id": "default", "label": "Start"}]
            },
            "x": 100,
            "y": 200
        },
        {
            "id": "premium-charge",
            "name": "Charge Premium Customer",
            "type": "TASK",
            "node_class": "BUSINESS",
            "node_definition_id": "<ND_PREMIUM_PAYMENT_ID>",
            "parameters": {
                "customer_id": "{{$input.customer_id}}",
                "email": "{{$input.customer_email}}",
                "amount": "{{$input.total_amount}}",
                "priority": "high",
                "expedited": true
            },
            "ports": {
                "in": [{"id": "in_default", "label": "Input"}],
                "out": [
                    {"id": "success", "label": "Success"},
                    {"id": "failure", "label": "Failure"}
                ]
            },
            "x": 400,
            "y": 200
        },
        {
            "id": "check-result",
            "name": "Check Payment Result",
            "type": "CONDITION",
            "parameters": {
                "expression": "{{$premium-charge.transaction_id}} != ''"
            },
            "ports": {
                "in": [{"id": "in_default", "label": "Input"}],
                "out": [
                    {"id": "true", "label": "Success"},
                    {"id": "false", "label": "Failed"}
                ]
            },
            "x": 700,
            "y": 200
        }
    ],
    "edges": [
        {
            "source_node_id": "trigger",
            "target_node_id": "premium-charge",
            "source_port_id": "default",
            "target_port_id": "in_default"
        },
        {
            "source_node_id": "premium-charge",
            "target_node_id": "check-result",
            "source_port_id": "success",
            "target_port_id": "in_default"
        }
    ],
    "metadata": {},
    "schema_version": "1.0.0"
}
```

### Runtime Flow

```
1. Run starts with input_data:
   {customer_id: "C-VIP-001", customer_email: "vip@example.com", total_amount: 999}

2. Trigger fires → premium-charge task created

3. TaskHandler processes premium-charge:
   a. Resolve input_binding from workflow state:
      resolved_input = {
        customer_id: "C-VIP-001",
        email: "vip@example.com",
        amount: 999,
        priority: "high",
        expedited: true
      }

   b. _apply_node_definition() reads merged _node_internals:
      - input_mapping resolves:
        {
          charge_amount: 999,
          customer_ref: "C-VIP-001",
          metadata: {priority: "high", expedited: true}
        }
      - locked_defaults merged:
        {gateway: "stripe", currency: "USD", payment_tier: "premium", premium_fee_percent: 5}
      - Final task input (sent to worker):
        {
          customer_id: "C-VIP-001",
          email: "vip@example.com",
          amount: 999,
          priority: "high",
          expedited: true,
          charge_amount: 999,
          customer_ref: "C-VIP-001",
          metadata: {priority: "high", expedited: true},
          gateway: "stripe",
          currency: "USD",
          payment_tier: "premium",
          premium_fee_percent: 5
        }

   c. edit_mode = "before_dispatch" → task set to PENDING (HITL)
      Workflow pauses. Frontend shows sectioned form with 3 sections.

   d. User reviews and confirms → task dispatches to payment-worker

4. Worker returns:
   {output: {transaction_id: "txn_abc", status: "succeeded", charged_amount: 999, fee: 49.95}}

5. CompleteTask applies output_mapping:
   {transaction_id: "txn_abc", charged_amount: 999}
   → Stored in run.context.variables["premium-charge"]
   → Also aliased under display name for expressions (not in output_data)

6. check-result evaluates: "txn_abc" != "" → true
   → Takes the "true" (Success) branch

7. Run completes → output_data contains only node-ID-keyed results + input.
   Name aliases ("Charge Premium Customer") and the "trigger" convenience
   key are excluded.
```

---

## Example 6: Full End-to-End Integration Test Data

This is a complete sequence you can run against a fresh local instance. Each step shows the exact HTTP request and the response you should verify.

### Setup: Start the service

```bash
cd workflow-control-plane-service
INFRA_MODE=mock python main.py
# Runs on http://localhost:8001
```

### Step 1: Create parent definition

```bash
curl -s -X POST http://localhost:8001/node-definitions \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "name": "Base HTTP Worker",
    "description": "Generic HTTP call wrapper",
    "parent_definition_id": null,
    "base_type": "TASK",
    "base_worker_id": "http-request-worker",
    "locked_defaults": {"timeout_ms": 30000},
    "input_schema": [
        {"key": "url", "type": "string", "label": "URL", "required": true},
        {"key": "method", "type": "string", "label": "Method", "required": true}
    ],
    "output_schema": [
        {"key": "status_code", "type": "number"},
        {"key": "body", "type": "object"}
    ],
    "input_mapping": {},
    "output_mapping": {
        "status_code": "{{$_output.output.status_code}}",
        "body": "{{$_output.output.body}}"
    },
    "icon": "Globe",
    "color": "#6366F1",
    "tags": ["http"],
    "data_mode": "json",
    "edit_mode": "none"
  }' | jq .
```

**Verify:** `data.error == ""`, save `data.id` as `$PARENT_ID`.

### Step 2: Create child definition (extends parent)

```bash
curl -s -X POST http://localhost:8001/node-definitions \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "name": "Get Users",
    "description": "Fetch users — locks method=GET, url=/users",
    "parent_definition_id": "'$PARENT_ID'",
    "base_type": "TASK",
    "base_worker_id": null,
    "locked_defaults": {
        "method": "GET",
        "url": "https://jsonplaceholder.typicode.com/users"
    },
    "input_schema": [
        {"key": "name_filter", "type": "string", "label": "Filter by Name"}
    ],
    "output_schema": [
        {"key": "users", "type": "array", "label": "Users"}
    ],
    "input_mapping": {
        "query_params": {"name": "{{$custom.name_filter}}"}
    },
    "output_mapping": {
        "users": "{{$_output.body.items}}"
    },
    "icon": "People",
    "color": "#10B981",
    "tags": ["users"],
    "data_mode": "json",
    "edit_mode": "none"
  }' | jq .
```

**Verify:**
- `data.error == ""`
- `data.parent_definition_id == $PARENT_ID`
- `data.base_worker_id == null` (will inherit from parent)
- Save `data.id` as `$CHILD_ID`

### Step 3: Verify the child via GET

```bash
curl -s http://localhost:8001/node-definitions/$CHILD_ID | jq .
```

**Verify:**
- `data.parent_definition_id == $PARENT_ID`
- `data.base_worker_id == null`

### Step 4: List all definitions for tenant

```bash
curl -s "http://localhost:8001/node-definitions?tenant_id=test-tenant" | jq .
```

**Verify:** `data.total == 2`, both definitions present with `parent_definition_id` in results.

### Step 5: Create a workflow using the child definition

```bash
curl -s -X POST http://localhost:8001/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "User Lookup Workflow",
    "description": "Fetches users using inherited business node",
    "input_schema": [
        {"key": "search_name", "type": "string", "label": "Search Name"}
    ],
    "nodes": [
        {
            "id": "trigger",
            "name": "Start",
            "type": "TRIGGER",
            "parameters": {"trigger_type": "manual"},
            "ports": {"in": [], "out": [{"id": "default", "label": "Start"}]},
            "x": 100, "y": 200
        },
        {
            "id": "get-users",
            "name": "Get Users",
            "type": "TASK",
            "node_class": "BUSINESS",
            "node_definition_id": "'$CHILD_ID'",
            "parameters": {
                "name_filter": "{{$input.search_name}}"
            },
            "ports": {
                "in": [{"id": "in_default", "label": "Input"}],
                "out": [{"id": "success", "label": "Done"}]
            },
            "x": 400, "y": 200
        }
    ],
    "edges": [
        {
            "source_node_id": "trigger",
            "target_node_id": "get-users",
            "source_port_id": "default",
            "target_port_id": "in_default"
        }
    ]
  }' | jq .
```

**Verify the enrichment in the response:**

The `get-users` node in the response should have `worker_id: "http-request-worker"` (auto-filled from resolved parent chain), even though the child definition has `base_worker_id: null`.

To verify the internal enrichment, fetch the workflow:

```bash
curl -s http://localhost:8001/workflows/$WORKFLOW_ID | jq '.data.nodes[] | select(.id == "get-users")'
```

The node's internal `config` (not exposed in the API response `parameters` since keys starting with `_` are excluded) now contains the merged inheritance chain.

### Step 6: Verify deletion protection

```bash
# Try to delete the parent — should fail because child references it
curl -s -X DELETE http://localhost:8001/node-definitions/$PARENT_ID | jq .
```

**Verify:** `data.error` contains `"Cannot delete: used as parent by Get Users"`.

### Step 7: Delete child first, then parent

```bash
# Delete child
curl -s -X DELETE http://localhost:8001/node-definitions/$CHILD_ID | jq .
# Verify: data.deleted == true

# Now delete parent
curl -s -X DELETE http://localhost:8001/node-definitions/$PARENT_ID | jq .
# Verify: data.deleted == true
```

### Step 8: Validation — cycle detection

```bash
# Create A
A_ID=$(curl -s -X POST http://localhost:8001/node-definitions \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "name": "Node A",
    "base_type": "TASK",
    "base_worker_id": "w1"
  }' | jq -r '.data.id')

# Create B extending A
B_ID=$(curl -s -X POST http://localhost:8001/node-definitions \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "name": "Node B",
    "parent_definition_id": "'$A_ID'",
    "base_type": "TASK"
  }' | jq -r '.data.id')

# Try to make A extend B (would create cycle A→B→A)
curl -s -X PATCH http://localhost:8001/node-definitions/$A_ID \
  -H "Content-Type: application/json" \
  -d '{"parent_definition_id": "'$B_ID'"}' | jq .
```

**Verify:** `data.error` contains `"Circular inheritance detected"`.

### Step 9: Validation — cross-tenant parent rejected

```bash
curl -s -X POST http://localhost:8001/node-definitions \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "other-tenant",
    "name": "Cross Tenant Child",
    "parent_definition_id": "'$A_ID'",
    "base_type": "TASK"
  }' | jq .
```

**Verify:** `data.error` contains `"Parent definition must belong to the same tenant"`.

---

## Merge Strategy Reference

When resolving a chain `[root, ..., leaf]`, each field is merged with a specific strategy:

| Field | Strategy | Details |
|-------|----------|---------|
| `locked_defaults` | **Shallow merge** | `{...parent, ...child}` — child keys override parent keys |
| `input_schema` | **Replace if non-empty** | Child's schema replaces parent's entirely (if `[]`, inherits parent) |
| `output_schema` | **Replace if non-empty** | Same as input_schema |
| `input_mapping` | **Shallow merge** | `{...parent, ...child}` — child keys override parent keys |
| `output_mapping` | **Shallow merge** | `{...parent, ...child}` — child keys override parent keys |
| `base_worker_id` | **Child wins if set** | If child has a non-null worker_id, it overrides; else inherits |
| `data_mode` | **Child wins if not `"json"`** | `"json"` is the default — only non-default values override |
| `edit_mode` | **Child wins if not `"none"`** | `"none"` is the default — only non-default values override |
| `icon` | **Child wins if non-empty** | Empty string means "inherit parent" |
| `color` | **Child wins if non-empty** | Empty string means "inherit parent" |
| `tags` | **Union (deduplicated)** | Parent tags + child tags, preserving order, no duplicates |

### Safety Limits

- **Max inheritance depth:** 10 levels
- **Cycle detection:** Raises `CircularInheritanceError`
- **Self-reference:** `parent_definition_id == id` is rejected at validation time
- **Cross-tenant:** Parent must belong to the same tenant
- **Deletion protection:** Cannot delete a definition that has children

---

## Expression Reference

### Available Sources in `input_mapping`

| Source | Meaning | Example |
|--------|---------|---------|
| `$custom` | The resolved `input_binding` values (what the user/workflow provided for this node) | `{{$custom.customer_id}}` |
| `$<node_id>` | Output of another node in the workflow | `{{$fetch-order.output.customer_id}}` |
| `$input` | Workflow input data | `{{$input.customer_id}}` |

### Available Sources in `output_mapping`

| Source | Meaning | Example |
|--------|---------|---------|
| `$_output` | The raw output returned by the worker | `{{$_output.output.body.items}}` |

### How `input_mapping` Works at Runtime

```
1. Engine resolves input_binding → resolved_input (e.g. {customer_id: "C-123", amount: 500})
2. Builds mapping_context = {...run.context.variables, "custom": resolved_input}
3. Resolves input_mapping expressions against mapping_context
4. Merges: {...resolved_input, ...mapped_values, ...locked_defaults}
   (locked_defaults always win)
```

### How `output_mapping` Works at Runtime

```
1. Worker returns raw output → task_output = {output: {...}, metadata: {...}}
2. Builds context = {"_output": task_output}
3. Resolves output_mapping expressions → new task_output
4. Stores in run.context.variables[node_id]
   Also aliased under node.name for expression resolution (internal only)
5. On run completion, output_data includes only node-ID-keyed entries + "input"
   (name aliases and the "trigger" convenience key are excluded)
```

---

## Sectioned Input Schemas

Use `type: "section"` to group fields into collapsible sections in the workflow editor:

```json
{
    "input_schema": [
        {
            "key": "customer_info",
            "type": "section",
            "label": "Customer Information",
            "fields": [
                {"key": "customer_id", "type": "string", "label": "Customer ID", "required": true},
                {"key": "email", "type": "string", "label": "Email Address"}
            ]
        },
        {
            "key": "order_config",
            "type": "section",
            "label": "Order Configuration",
            "fields": [
                {"key": "limit", "type": "number", "label": "Max Items", "default": 50},
                {"key": "sort_by", "type": "string", "label": "Sort By", "default": "created_at"}
            ]
        }
    ]
}
```

**Note:** The `key` for sections is used as a grouping identifier. The actual user-provided values use the nested `fields[].key` values (e.g., `customer_id`, `email`). In `input_mapping`, reference them as `{{$custom.customer_id}}`, not `{{$custom.customer_info.customer_id}}`.

---

## Troubleshooting

### "base_worker_id is required when base_type is TASK"

This error occurs when creating a TASK definition without a `base_worker_id` and without a `parent_definition_id`. If using inheritance, set `parent_definition_id` and leave `base_worker_id` as `null` — it will inherit from the parent.

### "Parent definition not found"

The `parent_definition_id` references a non-existent definition. Check the ID is correct and the parent was created first.

### "Parent definition must belong to the same tenant"

Cross-tenant inheritance is not allowed. Both parent and child must have the same `tenant_id`.

### "Circular inheritance detected"

You created a cycle (A → B → A). Break the cycle by removing or changing one of the `parent_definition_id` references.

### "Cannot delete: used as parent by ..."

Delete the child definitions first before deleting the parent.

### Worker ID not auto-populated in workflow

The `worker_id` is only auto-filled if the workflow node's `worker_id` is empty/null. If you explicitly set `worker_id` on the workflow node, it takes precedence over the definition's `base_worker_id`.

### `_node_definition` / `_node_internals` not visible in workflow response

These are internal config keys prefixed with `_`. The `merge_parameters()` function excludes them from the `parameters` field in API responses. They are stored internally in `node.config` and read by the runtime engine.
