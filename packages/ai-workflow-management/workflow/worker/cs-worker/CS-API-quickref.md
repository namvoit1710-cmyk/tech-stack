# CS Log Analysis API — quick reference

**Base URL** (not the editor host):

```
https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com
```

| Workflow | `workflow_id` | Output node |
|---|---|---|
| Log flow — you know the CR, project, env | `3ad8ac75-f712-409c-b8dd-73b05ee5529f` | `flow_out`, or `flow_out_plain` when you skip the summary — see §4 |
| Dispatcher — free-text message | `98a64446-875f-4002-9f7a-28dfe06dbdb8` | `out` |
| **Cancel CR** — you know the CR, project, env — see §6 | `26df8687-df15-499b-a1b0-95e05cb1e8b5` | `out` |

Valid targets: **ALCON** (DEV, QAS) · **MCK** (DEV, QAS) · **S4** (QAS)

> **ALCON QAS was not answering on 2026-08-27** — its OAuth client was rejected
> (`invalid_client`), so calls against it came back with empty logs and, per §5, a
> `COMPLETED` run. Not re-checked since; treat it as suspect rather than as fact.

---

## 1. Start — `POST /api/v1/runs`

Log flow:

```json
{
  "workflow_id": "3ad8ac75-f712-409c-b8dd-73b05ee5529f",
  "tenant_id": "system",
  "input_data": {
    "reqID": "CR0000000134",
    "project": "ALCON",
    "environment": "DEV"
  }
}
```

Log flow, **logs only — no analysis**:

```json
{
  "workflow_id": "3ad8ac75-f712-409c-b8dd-73b05ee5529f",
  "tenant_id": "system",
  "input_data": {
    "reqID": "CR0000000134",
    "project": "ALCON",
    "environment": "DEV",
    "summarize": false
  }
}
```

Dispatcher:

```json
{
  "workflow_id": "98a64446-875f-4002-9f7a-28dfe06dbdb8",
  "tenant_id": "system",
  "input_data": {
    "message": "CR0000000134 in ALCON project with DEV environment"
  }
}
```

Response → `data.id` is the run id. Optional: `"correlation_id": "<your ref>"`.

## 2. Poll — `GET /api/v1/runs/{run_id}`

```json
{ "data": { "status": "COMPLETED", "error": null, "output_data": { "flow_out": "..." } } }
```

Stop on `COMPLETED` / `FAILED` / `ERROR` / `CANCELLED`. Poll every 3–5 s.

⚠️ `output_data` is a **file reference**, not the answer. Get the answer from step 3.
Its single key, though, is the name of the output node that ran — use it in step 3.

## 3. Read — `GET /api/v1/runs/{run_id}/nodes/{output_node}/output`

```json
{ "data": { "iterations": [ { "scopes": [ { "paths": [
  { "path": "output.summary", "sample": "Requirement: The customer-service team asked…" }
] } ] } ] } }
```

Walk `iterations[] → scopes[] → paths[]`, keep entries whose `path` starts with `output.`,
strip that prefix.

Cancel early: `POST /api/v1/runs/{run_id}/cancel`

⚠️ That cancels **the run** — this API call, stopped mid-flight. It has nothing to do
with cancelling a **change request** in the SAP system, which is §6. The two are
unrelated and share a word.

---

## 4. Skipping the analysis

Send `"summarize": false` and the run fetches the logs, uploads them, and stops — no
model call. **33 s instead of 53 s.**

Only `false` skips it (any case, string or boolean). Anything else — including leaving
the field out entirely — produces the analysis, so callers written before this flag
existed keep working untouched.

**The output node changes with it.** A skipped run answers from `flow_out_plain`, an
analysed run from `flow_out`. Do not hardcode either one:

```
node = Object.keys(run.output_data)[0]        # "flow_out" or "flow_out_plain"
GET /api/v1/runs/{run_id}/nodes/{node}/output
```

Asking for the node that did **not** run returns `HTTP 200` with an empty `paths` list —
no error, no warning. A client that hardcodes `flow_out` and then sends `summarize:false`
gets a successful-looking response containing nothing at all.

Both nodes return the same field names. On a skipped run `summary` is the single line
`Summary skipped (summarize=false).` followed by the usual attachment block, so the
download link and the CR link are still there.

---

## 5. A landscape that is down still answers `COMPLETED`

Worth reading twice. The log fetchers never raise: a 401, a 404 or a DNS failure comes
back as *data*, and the run finishes normally.

```
project=ALCON environment=QAS   ->  status COMPLETED, error null, 30 s
```

Nothing in `status` or `error` says the landscape refused us. Check the payload instead:

* each fetch node carries its own `status` (`OK` / `FAILED`) and `error` text, and
* the uploaded logs file holds the actual responses.

An analysis written over empty logs reads plausibly and says almost nothing — the model
was given nothing to work with and will not tell you so. If an answer looks oddly thin,
open `logs_url` before trusting it.

`cr_url` comes back empty, never guessed, when the landscape has no web app or the
fetch failed. An empty value means "no link" — not "broken link".

---

---

## 6. Cancelling a change request

**This changes the customer's system and cannot be undone.** Everything else in this
document reads; this one writes. There is no undo, no dry run and no confirmation step.

### The flow

```json
{
  "workflow_id": "26df8687-df15-499b-a1b0-95e05cb1e8b5",
  "tenant_id": "system",
  "input_data": {
    "reqID": "CR0000031234",
    "project": "S4",
    "environment": "QAS",
    "comment": "why it is being cancelled"
  }
}
```

Read it back from node `out`: `reply` (a sentence for a human, with the CR linked),
`ok` (boolean), `reqID`.

`comment` is stored on the request and read by whoever opens it later, so write a reason,
not a placeholder.

### End to end

The output node is **always `out`** for this flow — unlike the log flow, whose node
changes with `summarize` (§4). Nothing to discover here; the three calls are fixed.

```bash
BASE=https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com
CANCEL_FLOW=26df8687-df15-499b-a1b0-95e05cb1e8b5

# 1. start it
RUN=$(curl -s -X POST "$BASE/api/v1/runs" \
  -H 'Content-Type: application/json' \
  -d '{"workflow_id":"'"$CANCEL_FLOW"'","tenant_id":"system",
       "input_data":{"reqID":"CR0000031234","project":"S4","environment":"QAS",
                     "comment":"duplicate of CR0000031200, cancelled by CS"}}' \
  | jq -r .data.id)

# 2. wait — 9-10 s is typical
while :; do
  ST=$(curl -s "$BASE/api/v1/runs/$RUN" | jq -r .data.status)
  case "$ST" in COMPLETED|FAILED|ERROR|CANCELLED) break;; esac
  sleep 2
done

# 3. read the answer
curl -s "$BASE/api/v1/runs/$RUN/nodes/out/output" | jq -r '
  .data.iterations[].scopes[].paths[]
  | select(.path | startswith("output."))
  | "\(.path[7:])\t\(.sample)"'
```

```
reply   ✅ [CR0000031234](https://smdg-s4-qas-simplemdg-web.cfapps.br10.hana.ondemand.com/main/index.html#/requestHistory/CR0000031234) has been cancelled on S4 QAS.
ok      true
reqID   CR0000031234
```

`$ST` from step 2 is about the **run**, not about the cancellation. Read `ok` from step 3
before telling anyone the request was cancelled — the next section is why.

### Gate on `ok`, never on the HTTP code

The node **never raises**. A 401, a 404 and a refusal all come back as *data* on the
success port, so — exactly as in §5 — a run that cancelled nothing still
reports `COMPLETED`.

| `ok` | `code` | What happened |
|---|---|---|
| `true` | `CANCELLED` | Cancelled. |
| `false` | the backend's own code, e.g. `CAN_NOT_LOCK_REQUEST` | Refused. Nothing is broken; the answer is no. |
| `false` | `CANCEL_ACTION_NOT_AVAILABLE` | That landscape's service is a build without the action. Cancel in the web app. |
| `false` | *(and `error` set, `status` = `FAILED`)* | A real fault — 401, 500, a wrong endpoint. |

`code` is deliberately not an English string: the backend words its `message` however it
likes and may reword it in any release. Compare the code.

### A CR that does not exist looks exactly like a locked one

Measured 2026-09-08 on S4 QAS with `CR9999999999`, which does not exist:

```
HTTP 400   code=CAN_NOT_LOCK_REQUEST   message="CAN_NOT lock request"   ok=false
```

The backend does **not** distinguish "no such request" from "cannot lock it right now", so
this API alone cannot tell you which one you hit. If you need that distinction, check the
CR exists first — the dispatcher below already does, and answers *"I could not find
CR… in the S4 QAS system"* instead of attempting the cancel.

`cr_url` is derived from the target, never probed, so it comes back populated even for a
CR that does not exist. A link is not evidence that the request is real.

### Through the dispatcher, in plain language

Send a message that plainly asks to cancel; `intent` comes back `cancel_request` and the
dispatcher routes to the cancel flow.

```json
{
  "workflow_id": "98a64446-875f-4002-9f7a-28dfe06dbdb8",
  "tenant_id": "system",
  "input_data": { "message": "cancel CR0000031234 on S4 QAS" }
}
```

The bar is an explicit instruction, and ambiguity resolves **towards reading logs**:

| Message | `intent` |
|---|---|
| `cancel CR0000031234 S4 QAS` · `huy CR0000031234 tren S4 QAS` | `cancel_request` |
| `why did CR0000031234 fail on S4 QAS?` | `analyze_log` |
| `CR0000031234 tren S4 QAS bi stuck a?` | `analyze_log` |

Wanting to know **why** a request failed, or whether it is stuck, is not a cancellation.
When the two readings are close the classifier picks `analyze_log`, because reading logs is
harmless and the user can ask again. A caller that needs certainty about what will happen
should use the cancel flow directly rather than the dispatcher.

The dispatcher's existence gate runs first, so a cancellation aimed at a CR that is not
there stops with a sentence and never reaches the cancel call.

### The endpoint underneath

```
POST https://<prefix>-srv-process-requestor.cfapps.<region>.hana.ondemand.com
     /RequestorService/cancelRequest
Body: {"reqID": "...", "comment": "..."}
Auth: client_credentials
```

This is the action the web app's own Cancel button performs. It exists on every landscape
checked — ALCON, MCK and S4 — and takes a plain service token.

Two things worth knowing if you call it directly:

* `CommonProcessService/cancelStuckRequest` is a **different** action, present only on the
  two MCK landscapes. The workflow deliberately does not use it: one action that works
  everywhere beats two with a fallback between them.
* `cancelMassRequest` and `cancelMassRequestItem` live on the same service and are
  **untested** — proving them needs a mass request somebody designates.

The app is `<prefix>-srv-process-requestor`, the same shape as
`<prefix>-srv-process-system`. A wrong host answers with a router error carrying no action
name; a landscape whose build lacks the action answers 404 *naming* it. That difference is
how `CANCEL_ACTION_NOT_AVAILABLE` is told apart from a typo in the host.

---

## Fields

**Log flow** — in: `reqID`, `project`, `environment`, `summarize` *(optional, default `true`)*
· out: `summary`, `logs_url`, `reqID`, `project`, `environment`, `cr_url`

**Cancel flow** — in: `reqID`, `project`, `environment`, `comment`
· out: `reply`, `ok` (boolean), `reqID`

**Dispatcher** — in: `message` (do **not** send `project`). The message decides
`summarize` too: "logs only", "no summary", "chỉ lấy log" skip the analysis.
· out: `reply`, `decision` (`run` | `stop`), `intent`, `target`

`intent` is one of `analyze_log` · `cancel_request` · `capabilities` · `greeting` ·
`other`. Only `cancel_request` reaches the cancel flow; §6 has how the line is drawn.

## Timing

| | |
|---|---|
| Log flow | 45–54 s |
| Log flow, `summarize: false` | 30–33 s |
| Dispatcher, declined | 20–25 s |
| Dispatcher, analysed | 61–81 s |
| Dispatcher, logs only | 52 s |
| Cancel flow | 9–13 s (the call itself ~1.3 s) |
| Dispatcher, cancel | 25–35 s |

Use a 5-minute timeout. On `expected version N`, retry once.

## Note

The control plane has **no authentication** — anyone with a `workflow_id` can start runs.
Keep these ids internal.

That matters more now than when this file described only reads: the id in §6 cancels
change requests in a customer's system, with no credential of any kind. Treat
`26df8687-…` as a secret, and do not put it in anything that runs in a browser.
