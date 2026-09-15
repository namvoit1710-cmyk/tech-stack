# Nested Sub-Workflow + Custom-Node Validation, and Real-Stack End-to-End Run

**Status:** Implemented & verified
**Date:** 2026-06-14
**Scope:** workflow-control-plane-service (engine nesting, custom NodeDefinition nodes, real-infra run)
**Branch:** feature/workflow/SA-846-cycle-execution

---

## 1. Overview

Two workflows were prepared and validated this cycle:

| # | Workflow | Purpose | Validated by | Status |
|---|----------|---------|--------------|--------|
| A | **5-level nested sub-workflows with custom nodes** | Exercise deep WORKFLOW-node nesting + custom `NodeDefinition` nodes + child→parent output propagation across 5 levels | In-memory integration test (queue-driven harness) | ✅ green |
| B | **Real-stack HTTP e2e** (`claude-real-e2e-http`) | Prove the *running* control plane executes a TASK through the real worker stack against real backends | Live run against HANA + Kafka + worker-executor + worker | ✅ COMPLETED |

Definitions are committed as reusable artifacts under
[`docs/design/workflows/`](./workflows/):
- `nested-5level-custom-nodes.workflows.json` — the 5-workflow bundle + 2 node definitions (Workflow A)
- `real-e2e-http.workflow.json` — the single TASK workflow (Workflow B)

---

## 2. Workflow A — 5-level nested sub-workflows with custom nodes

### 2.1 Shape

A Russian-doll chain of five workflows (`wf-l1` → … → `wf-l5`), each level invoking the
next via a `WORKFLOW` node. Every level carries a rich node mix:

```
Ln (n = 1..4):
   (l1-trig →) l{n}-ha   HUMAN_ACTION
     → l{n}-cond  CONDITION   '{{$l{n}-ha._action}} == "confirm"'
          true_branch  → l{n}-biz   TASK (custom node, node_definition_id)
          false_branch → l{n}-rej   OUTPUT (reject leaf)
   l{n}-biz → l{n}-compute  COMPUTE   '{{$l{n}-biz.summary}} + {n}'
   l{n}-compute → l{n}-switch  SWITCH  (rule: $l{n}-ha._action == "confirm" → branch "go")
          port "go"      → l{n}-wf   WORKFLOW → wf-l{n+1}
          port "default" → l{n}-side OUTPUT (never taken on the spine)
   l{n}-wf → l{n}-out  OUTPUT

L5 (deepest): identical minus the WORKFLOW hop — l5-switch "go" → l5-out.
```

Node types exercised per level: `HUMAN_ACTION`, `CONDITION`, `TASK` (custom), `COMPUTE`,
`SWITCH`, `WORKFLOW`, `OUTPUT` (+ `TRIGGER` at L1).

### 2.2 Custom nodes (NodeDefinition)

The `*-biz` TASK nodes are **business-node templates** resolved from a registered
`NodeDefinition` (not raw worker nodes). At save time the resolver stamps a
`definition_snapshot`; at runtime the engine applies it:

- `locked_defaults` (`method`) win over user input — applied at **dispatch**.
- `input_mapping` (`payload = {{$custom.note}}`) maps the node's own input — at **dispatch**.
- `output_mapping` (`summary`, `tag`) reshapes the worker output — at **completion**.

`nd-business-l5` inherits from `nd-business` (parent chain) and overrides only `method`
(`POST` → `PATCH`), exercising the resolver's `_merge_chain`.

### 2.3 Output propagation contract

The deepest custom node's value is carried to the root through the OUTPUT-node child→parent
contract. Each level's OUTPUT exposes a `deepest` field; for `n < 5` it forwards the child's
(`{{$l{n}-wf.output.deepest}}`), and L5 seeds it from its own custom node
(`{{$l5-biz.tag}}`). The root run's `output_data["deepest"]` therefore equals the L5 marker
(`"L5-CUSTOM-OK"`) once the chain completes.

> **Note (strict OUTPUT resolution):** the engine fails an OUTPUT node if a referenced path
> can't resolve. So the reject/side leaves also expose `deepest` — otherwise a parent level's
> OUTPUT (which references `…wf.output.deepest`) would fail when a deeper level took a leaf.

### 2.4 Definition

Full bundle: [`workflows/nested-5level-custom-nodes.workflows.json`](./workflows/nested-5level-custom-nodes.workflows.json)
(2 node definitions + 5 workflows). The integration test seeds it with **explicit** ids
(`wf-l1..wf-l5`) and stamps the snapshot via the production `NodeDefinitionResolver`. Over the
public API, create **bottom-up** (`wf-l5` first) and substitute each generated workflow id into
the parent's `config.workflow_id`.

### 2.5 Status — ✅ green

Source: `tests/integration/test_nested_child_workflows_5layer_custom_nodes.py` (2 tests).

- `test_five_level_chain_with_custom_nodes_cascades_and_propagates` — confirm at every level;
  asserts all 5 runs reach `COMPLETED`, `method` = `POST` (L1–L4) / `PATCH` (L5 via
  inheritance), `input_mapping` applied, CONDITION `true_branch` + SWITCH `go` recorded, and
  `output_data["deepest"] == "L5-CUSTOM-OK"` at the root.
- `test_reject_at_l3_short_circuits_l4_and_l5` — reject at L3; L4/L5 never spawn; L3/L2/L1 still
  `COMPLETED` via the reject leaf; deepest is not the L5 marker.

```
tests/integration/test_nested_child_workflows_5layer_custom_nodes.py … 2 passed
full integration suite ……………………………………………………………… 379 passed, 17 skipped
```

---

## 3. Workflow B — real-stack HTTP end-to-end (`claude-real-e2e-http`)

### 3.1 Shape & definition

`TRIGGER → TASK(worker_type=http_request)`. The TASK dispatches to the **real** http_request
worker, which performs an actual HTTP `GET` and returns the response into the node output.
Definition: [`workflows/real-e2e-http.workflow.json`](./workflows/real-e2e-http.workflow.json).
(The probe target is the CP's own `/api/v1/health` — a guaranteed-reachable real HTTP call.)

### 3.2 Live stack

| Service | Port | Role |
|---|---|---|
| workflow-control-plane-service (`main.py`) | `:8001` | REST API + orchestration engine (`/api/v1`) |
| worker-executor-service | `:8004` | worker registry + task dispatch target; reports results to CP |
| http-request-worker | `:35000` | registered `worker_type=http_request`; performs the HTTP call |

Infra: `INFRA_MODE=local` → **real HANA** persistence (localhost:39041, migrations ran) + **real
Kafka** orchestration bus (localhost:9092); the CP additionally points at the real SAP **-dev**
file-service / data-factory / governance / push-gateway. `EVENT_LOG_BACKEND=memory` (ephemeral
projector). See [`workflows/`](./workflows/) and the run-book in §5.

### 3.3 Status — ✅ COMPLETED

- Workflow created via `POST /api/v1/workflows` → id `e6f9db08-b054-42c5-9483-1de4bb34ec7a`.
- Run started via `POST /api/v1/runs` → id `020d3657-1be0-48ec-abe9-3b9987612fb4`:
  `RUNNING → COMPLETED` (~5 s).
- Dispatch chain proven in the worker log:
  `POST /api/v1/execute-async 202` → `Executing HTTP request task {task_id: …-call}` →
  `HTTP GET http://localhost:8001/api/v1/health`.
- Node `call` output (`GET /api/v1/runs/{id}/nodes/call/output`): both nodes `COMPLETED`,
  `output_port = success`, captured **`status_code = 200`**, plus `response_body`,
  `is_success`, `content_type`, `elapsed_ms`, `final_url`.

```
POST /api/v1/workflows                          → 200  (e6f9db08…)
POST /api/v1/runs {workflow_id}                 → 200  (run 020d3657…, RUNNING)
GET  /api/v1/runs/020d3657…                     → COMPLETED
GET  /api/v1/runs/020d3657…/nodes/call/output   → status_code=200, output_port=success
```

---

## 4. Read/write API surface verified (real HANA)

| Call | Result |
|---|---|
| `GET /api/v1/health` | 200 `{"status":"ok"}` |
| `GET /api/v1/workflows` | 200 — real persisted workflows from HANA |
| `GET /api/v1/runs` | 200 |
| `POST /api/v1/workflows` (+ read-back, + list) | 200 — persisted to HANA |
| `GET /api/v1/health/eventmesh` | 200 (`configured: true`, Kafka transport) |

---

## 5. Run-book — reproduce the real stack

```bash
# 0. Prereqs already up on this box: Kafka :9092, Redis :6379.

# 1. Control plane (real HANA + Kafka + SAP -dev services)
cd workflow-control-plane-service
set -a; source ../.env; set +a          # INFRA_MODE=local, KAFKA_BOOTSTRAP_SERVERS, FILE/DATA URLs
export PYTHONPATH="$(pwd)"               # the .env's PYTHONPATH=. resolves wrong
python3 main.py                          # uvicorn on :8001

# 2. Worker executor (registry + dispatcher)
cd ../worker-executor-service
export PYTHONPATH="$(pwd)" INFRA_MODE=mock TRANSPORT_MODE=http SERVICE_PORT=8004 \
       WORKFLOW_CONTROL_PLANE_URL=http://localhost:8001
python3 main.py                          # uvicorn on :8004

# 3. http_request worker — DO NOT `source .env` (CRLF appends \r to WORKER_TYPE,
#    breaking the worker_type match at dispatch). Set vars explicitly:
cd ../worker/http-request-worker
export PYTHONPATH="$(pwd)" WORKER_TYPE=http_request WORKER_NAME="HTTP Request Worker" \
       SERVER_PORT=35000 REGISTRY_URL=http://localhost:8004
python3 main.py                          # uvicorn on :35000, registers to :8004

# 4. Drive the e2e workflow
BASE=http://localhost:8001/api/v1
WF=$(curl -s -XPOST $BASE/workflows -H 'Content-Type: application/json' \
      -d @docs/design/workflows/real-e2e-http.workflow.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["id"])')
curl -s -XPOST $BASE/runs -H 'Content-Type: application/json' -d "{\"workflow_id\":\"$WF\",\"input_data\":{}}"
# poll GET $BASE/runs/{run_id} until COMPLETED; read GET $BASE/runs/{run_id}/nodes/call/output
```

> Stop services by killing the per-service `python3 main.py` process matched by
> `/proc/<pid>/cwd` (all three share the `main.py` cmdline). Avoid `kill -9 -<pgid>`:
> orphaned background processes reparent to the harness subreaper.

---

## 6. Findings & follow-ups

1. **CRLF in worker `.env` files** (`worker/*/.env`) — Windows line endings make `source`
   append `\r` to `WORKER_TYPE`, so the worker registers as `"http_request\r"` and the CP's
   `worker_type="http_request"` dispatch never matches. Normalize the `.env` line endings (or
   strip `\r` in the worker bootstrap) so the stack runs without the manual override above.
2. **`local` mode connects to HANA when `HANA_HOST` is set** — despite `.env.example` framing
   `local` as "memory repos", `HANA_HOST=localhost:39041` is reachable here, so it ran as real
   HANA persistence. Intended for this dev box; note it when expecting memory repos.
3. **Test artifacts in dev HANA** — `claude-real-api-smoke` and `claude-real-e2e-http`
   (+ run `020d3657`) were created during validation; delete if undesired.
4. **Live 5-level run (optional next step)** — Workflow A is verified in-process. A *live* run
   would need the custom node's `base_worker_id` pointed at a registered worker (e.g.
   `http_request`) and bottom-up workflow creation to wire child ids; the HUMAN_ACTION gates
   require `POST /runs/{id}/nodes/{node}/submit` (resume) per level.
```
