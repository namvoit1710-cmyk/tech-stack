# CS Log Analysis API

Run a change-request log analysis over HTTP — the same thing the CS Bot does in Teams,
without Teams.

Every payload, latency and response below was measured against the live tenant-1
workflows on **24 August 2026**, after the worker moved to the SDK's multi-type lease runner.

---

## 1. The base URL is not the one in your browser

The link you follow to look at a workflow is the **editor UI**. It cannot be called from
code — it is a single-page app, and its host serves no API.

| | |
|---|---|
| Editor (browser) | `smdg-ai-tenant-1-frontend-workflow-management.cfapps.br10.hana.ondemand.com` |
| **API (your code)** | `smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com` |

The UUID at the end of an editor URL is the `workflow_id` you post to the API. That part
carries over unchanged.

> **The control plane accepts unauthenticated reads, writes and deletes from the public
> internet.** No token, no header, no allow-list. That is what makes the calls below so
> short, and it means anyone who learns a workflow id can start runs against it. Treat
> these URLs as internal, keep them off anything public-facing, and put your own service
> in front if untrusted callers are ever involved.

---

## 2. Which of the two workflows to call

They differ in one thing: whether *you* already know the project, the environment and the
change request, or whether you are handing over a sentence a human typed.

### Log flow — call this one by default

```
3ad8ac75-f712-409c-b8dd-73b05ee5529f     answer node: flow_out
```

You pass `reqID`, `project`, `environment`. It fetches the logs and writes the analysis.
No language model reads your input, so it is faster, cheaper and deterministic.

Add `"summarize": false` when you only want the raw logs — the run then skips the
model entirely.

**Measured: 45–54 s; 33 s with `summarize=false`.**

### Dispatcher — when a human typed the request

```
98a64446-875f-4002-9f7a-28dfe06dbdb8     answer node: out
```

You pass one `message`. It works out the project, environment and CR, checks all three
exist, and either runs the analysis or returns a plain-language reason it did not.

**Measured: 20–25 s when it declines, 61–76 s when it runs.**

### Only configured targets work, on both

Today that is **ALCON** (DEV, QAS), **MCK** (DEV, QAS) and **S4** (QAS). The log flow
answers an unconfigured pair with a `FAILED` node that names what *is* configured; the
dispatcher answers in a sentence.

Adding a project is four environment variables on the cs-worker and a container restart —
no change to either workflow, and no change to your call.

---

## 3. The three calls

Start, poll, read. There is no synchronous option — the work takes minutes, so a run is a
job, not a request.

### Step 1 — start the run

Only `workflow_id` is required. `tenant_id` defaults to `"system"`. Set `correlation_id`
to your own reference if you want to tie a run back to your request later.

```bash
curl -sX POST \
  "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com/api/v1/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "3ad8ac75-f712-409c-b8dd-73b05ee5529f",
    "tenant_id": "system",
    "correlation_id": "ticket-4821",
    "input_data": {
      "reqID": "CR0000000134",
      "project": "ALCON",
      "environment": "DEV"
    }
  }'
```

```json
{ "status": "ok", "data": { "id": "6120aa3d-…", "status": "CREATED" } }
```

`data.id` is the run id. Everything after this uses it.

### Step 2 — poll until it finishes

Every 3–5 seconds is plenty. Stop on `COMPLETED`, `FAILED`, `ERROR` or `CANCELLED`; give
up after about 5 minutes. `POST /api/v1/runs/{id}/cancel` stops one early.

```
GET /api/v1/runs/{run_id}
```

```json
{
  "status": "ok",
  "data": {
    "id": "6120aa3d-…",
    "status": "COMPLETED",
    "error": null,
    "started_at": "…",
    "completed_at": "…",
    "output_data": {
      "flow_out": { "__file_ref": true, "file_id": "file_01a0…", "columns": [...] }
    }
  }
}
```

> **`output_data` is a file reference, not your answer.** It describes *where* the values
> were written — column names and a `file_id`, never the text. Reading it as the result is
> the single most common way to get this wrong. The values come from step 3.

### Step 3 — read the answer off the output node

```
GET /api/v1/runs/{run_id}/nodes/flow_out/output      # log flow
GET /api/v1/runs/{run_id}/nodes/out/output           # dispatcher
```

```json
{
  "data": {
    "iterations": [ {
      "scopes": [ {
        "paths": [
          { "path": "output.summary",     "sample": "Requirement: The customer-service team asked…" },
          { "path": "output.logs_url",    "sample": "https://…/api/v1/download/file_01a0…" },
          { "path": "output.reqID",       "sample": "CR0000000134" },
          { "path": "output.project",     "sample": "ALCON" },
          { "path": "output.environment", "sample": "DEV" }
        ] } ] } ]
  }
}
```

Walk `iterations[] → scopes[] → paths[]` and keep the entries whose `path` starts with
`output.`. That flattens to the field names below.

---

## 4. Payload reference

### Log flow — `3ad8ac75-f712-409c-b8dd-73b05ee5529f`

| | Field | Meaning |
|---|---|---|
| in | `reqID` | The change request, e.g. `CR0000000134`. Passed to the log endpoints verbatim. |
| in | `project` | `ALCON`, `MCK`, `S4`. Case-insensitive. |
| in | `environment` | `DEV`, `QAS`. `QA` and `PROD`-style spellings are normalised. |
| in | `summarize` | **Optional, defaults to `true`.** Send `false` to skip the Claude analysis and get the collected logs alone — about 33 s instead of 53 s. Only `false` skips (any case); anything else, including omitting the field, summarises. |
| out | `summary` | The analysis: the failing step, the cause, and whether it is a data fix or an escalation. With `summarize=false` this is the single line `Summary skipped (summarize=false).` followed by the same attachment block. |
| out | `logs_url` | A download link to every raw log collected, as one Markdown file. |
| out | `reqID` · `project` · `environment` | Echoed back, so a queued job carries its own context. |

### Dispatcher — `98a64446-875f-4002-9f7a-28dfe06dbdb8`

| | Field | Meaning |
|---|---|---|
| in | `message` | What the person wrote, in any language. Everything else is derived from it, including whether to analyse: "logs only", "no summary", "chỉ lấy log" skip the analysis. |
| in | `text` | Optional alias, accepted for callers that already speak the Teams shape. Send the same string. |
| out | `reply` | The message to show. Formatted for chat when it ran; a plain sentence when it declined. |
| out | `decision` | `run` or `stop`. Branch on this, not on the wording of `reply`. |
| out | `intent` | `analyze_log`, `capabilities`, `greeting`, `other`. |
| out | `target` | What it resolved, e.g. `ALCON_DEV`; `None` when it could not. |

> Do **not** send a `project` field to the dispatcher. It exists in the schema for the
> Teams router, where it carries the *bot slug*, not the SMDG project — the dispatcher
> works out the project from the message.

---

## 5. Worked examples

Real runs, real timings, trimmed only for length.

### A change request that exists — log flow, COMPLETED

```
in  → {"reqID": "CR0000000134", "project": "ALCON", "environment": "DEV"}

out ← summary     = "Requirement: The customer-service team asked for an analysis of why change request
                     CR0000000134 (project ALCON, environment DEV) failed during the
                     submit/validation process…
                     Analysis summary: Two of the five validation steps failed.
                     validateAuthorization, validateDuplication and validateParallelChange
                     all PASSED. validateTemplatePayloadData FAILED because…"
      logs_url    = "https://…-file-service…/api/v1/download/file_01a03342-…"
      reqID       = "CR0000000134"
      project     = "ALCON"
      environment = "DEV"
```

### A change request that does not exist — dispatcher, `decision = stop`

```
in  → {"message": "CR0000000000 in ALCON project with DEV environment"}

out ← reply    = "I could not find **CR0000000000** in the ALCON DEV system - there is
                  no request with that number. Could you double-check it?

                  If the number is right, the request may live in a different environment."
      decision = "stop"
      intent   = "analyze_log"
      target   = "ALCON_DEV"
```

A quarter of the time of a full analysis, because the gate checks the change request
exists before any log is fetched. Without it the flow runs to completion and
produces a confident analysis of nothing.

### A project we do not serve, asked in Vietnamese — dispatcher, `decision = stop`

```
in  → {"message": "xem log CR0000004540 cua project VINFAST moi truong DEV"}

out ← reply    = "I don't have access to a project called **VINFAST**. I can analyse
                  logs for: ALCON (DEV, QAS); MCK (DEV, QAS); S4 (QAS). Which one did
                  you mean?"
      decision = "stop"
      target   = None
```

---

## 6. What will bite you

**Treating it as request/response.** The analysis is two OData round trips plus a
language-model call. A 30-second HTTP client will time out on every successful run. Queue
it, or poll.

**Reading `output_data`.** It is a descriptor — `__file_ref`, a `file_id`, column names.
Your answer is at `/nodes/{node}/output`.

**A green run that answers nothing.** A failed log fetch is returned as *data*, not as a
failure: `status: "FAILED"` inside a node that the engine still reports as successful. On
the log flow, check that `summary` actually names a step. The dispatcher's gate already
does this for you.

**`expected version N`.** An intermittent optimistic-concurrency conflict in the engine,
seen on runs called as a child workflow. Retry once; it is not caused by your payload.

**Both workflows are `PUBLISHED`** (24 Aug 2026), so what you call is frozen: an edit now
needs a deliberate re-publish rather than taking effect the moment someone saves. Note
that a re-publish **cancels that workflow's in-flight runs** — if you are running these on
a schedule, expect an occasional cancelled run when the flow is updated.

---

## 7. Client script

Standard library only, Python 3.8+. Start, poll, read — the whole contract in one file.
It ships in the repo as `cs-worker/workflow/call_cs_api.py`.

```python
BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
LOG_FLOW   = "3ad8ac75-f712-409c-b8dd-73b05ee5529f"
DISPATCHER = "98a64446-875f-4002-9f7a-28dfe06dbdb8"
OUTPUT_NODE = {LOG_FLOW: "flow_out", DISPATCHER: "out"}
TERMINAL = ("COMPLETED", "FAILED", "ERROR", "CANCELLED")


def start(workflow_id, input_data, correlation_id=""):
    body = {"workflow_id": workflow_id, "tenant_id": "system", "input_data": input_data}
    if correlation_id:
        body["correlation_id"] = correlation_id
    return _post("/api/v1/runs", body)["id"]


def wait(run_id, timeout_s=420, interval_s=4):
    deadline = time.monotonic() + timeout_s
    while True:
        run = _get(f"/api/v1/runs/{run_id}")
        if str(run["status"]).upper() in TERMINAL:
            return run
        if time.monotonic() > deadline:
            raise TimeoutError(f"run {run_id} still {run['status']}")
        time.sleep(interval_s)


def outputs(run_id, node_id):
    """The node's real values, flattened: {"summary": "...", "logs_url": "..."}."""
    data = _get(f"/api/v1/runs/{run_id}/nodes/{node_id}/output")
    out = {}
    for it in data.get("iterations") or []:
        for sc in it.get("scopes") or []:
            for pa in sc.get("paths") or []:
                if pa["path"].startswith("output."):
                    out[pa["path"][7:]] = pa.get("sample")
    return out
```

```bash
python call_cs_api.py log  CR0000000134 ALCON DEV
python call_cs_api.py chat "CR0000000134 in ALCON project with DEV environment"
```

---

Workflow definitions, the cs-worker and this client live in `simplemdg_ai` under
`apps/backend/ai-workflow-management/workflow/worker/cs-worker/`.
