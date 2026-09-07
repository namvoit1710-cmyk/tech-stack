# Data Migration API

The endpoint Integration Hub calls to run a rule migration against a HANA source.

```
POST /api/v1/data-migration/execute           the dispatch, verbatim
GET  /api/v1/data-migration/jobs/{id}         this job's progress
GET  /api/v1/data-migration/jobs/{id}/events  the same progress, as it happens (SSE)
```

A real run against MARA takes tens of seconds, so `execute` answers **202 immediately**
and works in the background. The result is **two tables in the source schema**, not a
response body — the HTTP call is a trigger, the tables are the delivery.

A listener can subscribe to `/events` and treat the terminal frame as the ack: it
carries the same body the callback POSTs. Read [Progress](#4-progress) for the two
caveats that come with depending on a live connection.

---

## 1. Dispatch — `POST /api/v1/data-migration/execute`

```jsonc
{
  "job_id": "EDAD7E50B12D454586BEB49F81CE2151",   // required, your idempotency key
  "source": {                                      // required
    "kind": "hana_virtual_table",                  // default; the only kind today
    "connection": {
      "host": "823f64e3-….hana.prod-br10.hanacloud.ondemand.com",
      "port": 443,
      "user": "USR_BVXCWNSER634I0OI8Q5W130H5",
      "schema": "USR_BVXCWNSER634I0OI8Q5W130H5",
      "password": "…",                             // optional — see Credentials
      "encrypt": true,
      "validate_cert": false
    },
    "credential": {                                // optional since 632c58f3a
      "scheme": "resolve-token",
      "token": "c34340d2-…",                       // single-use
      "resolve_url": "http://ih-host/api/v1/data-migration/connection-secret"
    },
    "virtual_table": "STAGING_VT_MARA"             // required; may be schema-qualified
  },
  "rules": [ /* … see §2 … */ ],
  "callback": {                                    // required
    "url": "http://ih-host/api/v1/data-migration/df-callback",
    "job_id": "EDAD7E50B12D454586BEB49F81CE2151"
  }
}
```

**`job_id` is idempotent.** Re-POSTing a job_id that already exists returns the existing
job untouched and does **not** re-run it. This is deliberate: the resolve-token is
single-use, so a retried dispatch would otherwise strand the job on a 403.

### Response — always `202`

```json
{
  "jobId": "EDAD7E50B12D454586BEB49F81CE2151",
  "status": "ACCEPTED",
  "reportTable": "DF_REPORT_EDAD7E50B12D454586BEB49F81CE2151",
  "callbackTable": "DF_CB_EDAD7E50B12D454586BEB49F81CE2151",
  "rowsRead": 0, "rowsPassed": 0, "rowsWritten": 0, "progressPct": 0.0,
  "rulesApplied": 0, "rulesViolated": 0, "errorRows": 0, "violations": {},
  "credentialSource": "", "ackedToIh": false, "ackError": null,
  "error": null, "elapsedSec": 0.0
}
```

A 202 means *accepted*, not *succeeded*. Both table names are known up front, so a
caller can record them before any work happens.

---

## 2. Rules

The same rule objects the validation API takes — `type`, `rule_name`, `error_message`,
`params`. Every type in [`rule_spec.py`](../app/layer3_adapters/controllers/restful/v1/dtos/rule_spec.py)
is accepted. A 126-rule production pack uses 12 of them:

`required` · `pattern` · `allowed_values` · `expression` · `length` · `range` ·
`conditional_required` · `compare_fields` · `set_unique` · `date_range` · `unique` ·
`case_when_expr` · `fuzzy_unique`

Validation rules flag rows; transform rules (`case_when_expr`) add columns. Example of each:

```jsonc
{ "type": "allowed_values", "rule_name": "MANDT is a known value",
  "error_message": "unexpected client",
  "params": { "columns": ["MANDT"], "values": ["120"], "case_sensitive": true } }

{ "type": "fuzzy_unique", "rule_name": "MATNR near-duplicate",
  "error_message": "near-duplicate material number",
  "params": { "columns": ["MATNR"], "method": "normalized" } }

{ "type": "conditional_required", "rule_name": "weight unit when weighed",
  "error_message": "Weight Unit is mandatory for a packaging material with a gross weight",
  "params": { "when": { "match": "ALL", "conditions": [
                          { "column": "MTART", "operator": "EQUAL", "value": "VERP" },
                          { "column": "BRGEW", "operator": "IS_NOT_EMPTY" } ] },
              "then": ["GEWEI"] } }

{ "type": "case_when_expr", "rule_name": "flag materials created from 2019 as active",
  "params": { "column_dest": "IsActive", "otherwise": 0,
              "conditions": [{ "when_expression": "pl.col('ERSDA') >= pl.lit('20190101')",
                               "result": 1 }] } }
```

**Every column a rule names must exist in the virtual table.** DF checks the schema
before reading a single row and fails the whole job if any is missing:

```
ValueError: 5 column(s) referenced by rules do not exist in STAGING_VT_MARA:
CREATED_AT_TIME, MATNR_EXTERNAL, PRD_ENDDT, PRD_STARTDT, SDM_VERSION
```

That is ~3 s of wasted work instead of a partial migration, but it means a drifted
source table takes the whole pack down. Check the pack against the source after any
source change.

### `when` conditions

`conditional_required` and the `derive` transform take the same `when`: one
`{column, operator, value}`, or `{match: ALL|ANY, conditions: [...]}`. `ALL` is the
default. Operators:

`EQUAL` · `NOT_EQUAL` · `IN` · `NOT_IN` · `CONTAINS` · `STARTS_WITH` ·
`GT` · `GTE` · `LT` · `LTE` · `IS_NULL` · `IS_EMPTY` · `IS_NOT_NULL` · `IS_NOT_EMPTY`

`eq` `ne` `lt` `le` `gt` `ge` still work and map onto the six comparisons.
`EQUAL`/`NOT_EQUAL` compare as **text**, so a leading zero survives (`"01" != "1"`);
`GT`/`GTE`/`LT`/`LTE` compare as **numbers**, because `"9" < "10"` is false as text.

An unrecognised operator is a 422 at the boundary, and so is a malformed condition
inside a `conditions` list. It is not a warning: dropping one condition from an
`ALL` group widens the trigger and dropping one from an `ANY` group narrows it, and
a narrowed mandatory rule returns **fewer** violations — a failure that reads as a
clean run.

---

## 3. Credentials

The HANA password is resolved from the first of these that is available. The job
reports which one it took as `credentialSource`, so it is never a guess:

| order | source | `credentialSource` |
|---|---|---|
| 1 | `source.connection.password` on the dispatch | `dispatch` |
| 2 | bound HANA service instance (`VCAP_SERVICES`) | `service-binding` |
| 3 | `DF_HANA_CREDENTIALS` / `DF_HANA_PASSWORD`, keyed `user@host` | `df-config` |
| 4 | `source.credential.resolve_url` — IH's token exchange | `resolve-token` |

If none is available the job fails in ~0.2 s and says so:

```
RuntimeError: no connection secret available for USR_…@823f64e3-….hanacloud.ondemand.com.
Supply one of: source.connection.password, a bound HANA service instance,
DF_HANA_CREDENTIALS / DF_HANA_PASSWORD, or source.credential.
```

> **Deployed today (dev / qas):** [`deployment/deployment.yml`](../deployment/deployment.yml)
> declares no `services:` binding and no `DF_HANA_CREDENTIALS`, so **paths 2 and 3 do not
> exist on the deployed app.** A migration there runs only via path 1 or path 4 — i.e. it
> still needs IH, or a caller willing to send the password. Path 3 was proven against a
> *local* DF. If "IH can be down" is meant to hold in production, that config is the gap.

---

## 4. Progress

`GET /api/v1/data-migration/jobs/{job_id}` returns the full job record. Poll it, or
stream the same thing from `/events` (§4.1).

```json
{
  "jobId": "EDAD7E50B12D454586BEB49F81CE2151",
  "status": "COMPLETED",
  "reportTable": "DF_REPORT_EDAD7E50B12D454586BEB49F81CE2151",
  "callbackTable": "DF_CB_EDAD7E50B12D454586BEB49F81CE2151",
  "rowsRead": 32843,
  "rowsPassed": 3448,
  "rowsWritten": 3448,
  "progressPct": 100.0,
  "rulesApplied": 120,
  "rulesViolated": 53,
  "errorRows": 101780,
  "violations": { "MTPOS_MARA is mandatory": 11698, "SPART is a known value": 9517 },
  "credentialSource": "dispatch",
  "ackedToIh": false,
  "ackError": "ConnectError: [Errno 111] Connection refused",
  "error": null,
  "elapsedSec": 20.74
}
```

| field | meaning |
|---|---|
| `status` | `ACCEPTED` → (`WAITING`) → `READING` → `VALIDATING` → `WRITING` → `COMPLETED`, or `FAILED` |
| `rowsRead` | rows pulled from the virtual table |
| `rowsPassed` | rows that satisfied every rule — the migration-ready count |
| `rowsWritten` | rows committed to the delivery table so far |
| `progressPct` | `rowsWritten / rowsPassed × 100` |
| `errorRows` | total violation *cells*, not rows — one row can violate many rules |
| `violations` | per-rule violation counts, `rule_name → n` |
| `error` | set only when `status` is `FAILED` |
| `ackedToIh` / `ackError` | whether the end-of-job notification reached IH |

`WAITING` means the container is over its memory ceiling (`DF_MEMORY_CEILING_PCT`,
default 80%) and this job is held at the door rather than piling onto it. It waits up
to `DF_ADMIT_WAIT_SEC` (default 120s), then fails with `MemoryPressure` naming the
reading and the limit. Memory, not connections, is what runs out first: every job holds
its columns in a Polars frame in one container, and an OOM fails **every** migration
running at that moment — so refusing one is the cheap outcome. It is admission control,
not enforcement: it cannot stop memory rising inside a job it already admitted.

**`progressPct` is 0 for most of the run.** It is defined against `rowsPassed`, which is
not known until validation finishes, so a real poll sequence looks like this — not a
smooth ramp:

```
  13.4s  WRITING     0.0%  read=32843 passed=3448 written=0
  24.9s  COMPLETED 100.0%  read=32843 passed=3448 written=3448
```

Poll every 2–10 s. `404` means the job_id is unknown to this instance.

A third channel exists and is the most robust of the three: **the delivery table's
catalog comment**. DF stamps `{job_id, status, rows_written, rows_expected,
report_table}` onto the table in the same transaction as the rows, so a reader gets a
consistent percentage from one catalog query and the two can never disagree — and it
still works after a DF restart, when the in-memory job store is gone.

---

## 4.1 `GET /jobs/{id}/events` — SSE

Subscribe **after** dispatch; a `404` means this instance has never seen that job_id.

```
curl -N https://…/api/v1/data-migration/jobs/EDAD7E50…/events
```

```
id: 1
event: progress
data: {"jobId":"EDAD…","status":"READING","rowsRead":0,…}

id: 2
event: progress
data: {"jobId":"EDAD…","status":"VALIDATING","rowsRead":32843,…}

: keepalive

id: 3
event: progress
data: {"jobId":"EDAD…","status":"WRITING","rowsPassed":3448,"rowsWritten":0,…}

id: 4
event: done
data: {"jobId":"EDAD…","columns":["MANDT","MATNR",…,"IsActive"],"rows":[],"done":true}
```

| event | meaning |
|---|---|
| `progress` | the full job record, same shape as `GET /jobs/{id}`; sent on every change |
| `done` | terminal. **Byte-identical to the callback POST body.** The rows are in the table |
| `failed` | terminal. `{"jobId", "error", "done": false}` — `done` is false because the rows are *not* there |
| `: keepalive` | a comment line, every 10 s of silence. Ignore it; SSE clients do automatically |

The first frame is the **current** state, sent immediately — a listener attaching to an
already-finished job gets the terminal frame at once rather than hanging.

### Two things to know before depending on it

**1. Silence is normal.** Only phase changes and write chunks produce frames, and the
phases are coarse. Measured on a real run (32,843 rows, 120 rules):

| phase | duration | frames |
|---|---|---|
| dispatch → first frame | 3.6 s | — |
| READING | 9.7 s | 1, at entry |
| VALIDATING | 1.0 s | 1, at entry |
| WRITING | 0.6 s | 1 at entry, then one per 5,000-row chunk |

`CHUNK_ROWS = 5000`, so 3,448 passing rows is a single chunk. That run streams **5
frames, and READING is one ~10 s quiet stretch** — it copies the federated source and
then reads it. At 2 M passing rows the write phase emits ~400 frames, but READING and
VALIDATING stay one frame each at any size: each is a single statement, and nothing can
be pushed from inside a running one. The heartbeat exists for exactly those gaps — CF
and the ALB close a connection that goes quiet, and the ALB header timeout is 22 s.

**2. A stream is not a delivery.** If the connection drops, the job keeps running and
finishes; only the listener is gone. So the terminal frame is a *notification*, and IH
should fall back to `GET /jobs/{id}` when the stream ends without one — otherwise a
dropped TCP connection turns a finished migration into one IH believes never completed.
The POST callback still fires, and remains the path that survives a dead subscriber.

Streaming costs nothing measurable. The same dispatch polled 20× more often (0.5 s vs
10 s) ran in 21.03 s against 20.74 s — inside the 18.69–21.03 s spread of four runs of
near-identical work. Emitting what is already known is free; what would cost wallclock
is chunking the read or the validate to manufacture finer frames.

> **Not SSE, but looks like it:** `POST /api/v1/validation` returns
> `application/json` whose first field is `_keep_alive`, padded with spaces and streamed
> to beat the same 22 s ALB timeout. It is one JSON document — parse it normally.

**Job status is in-memory and per-process.** A DF restart loses it; the tables survive.
That is the reason results go to the database rather than to memory. It also means both
`/jobs/{id}` and `/events` must reach the instance that took the dispatch — fine at
`instances: 1`, a routing problem the day dev or qas is scaled out.

---

## 5. The callback

The same message as the stream's `done` frame — both are built by `ack_payload()`, so
they cannot drift apart. When both tables are written and committed, DF POSTs **once**
to `callback.url`:

```json
{ "jobId": "EDAD7E50B12D454586BEB49F81CE2151",
  "columns": ["MANDT", "MATNR", "ERSDA", "…", "IsActive"],
  "rows": [],
  "done": true }
```

`rows` is empty on purpose — the rows are in the table. `columns` must **not** be empty:
IH sizes the target from it, so an empty list would create the target without any
rule-added column.

**A failed callback cannot fail a finished job.** By the time it fires, both tables are
committed and the migration has succeeded; reporting `FAILED` because IH was down would
be a lie about the data. The failure is recorded and the job stays `COMPLETED`:

```json
"status": "COMPLETED", "ackedToIh": false,
"ackError": "ConnectError: [Errno 111] Connection refused", "error": null
```

IH collects the tables by `job_id` whenever it comes back. `200` and `202` both count as
acked.

---

## 6. Output — two tables

| table | contents |
|---|---|
| `DF_REPORT_<job_id>` | every row read, with a verdict and the violations against it |
| `DF_CB_<job_id>` | only rows that passed every rule, plus rule-added columns — the migration-ready set |

Both live in the source schema. They are the deliverable; the HTTP response is not.

---

## 7. Testing it end to end

The deployed app has no HANA credential of its own (§3), so send the password on the
dispatch — which is what IH does. This reads DF's own `.env` and never prints the secret:

```python
import json, time, uuid, requests
from dotenv import dotenv_values

DF  = "https://smdg-ai-tenant-1-data-factory.cfapps.br10.hana.ondemand.com"
cfg = dotenv_values("apps/backend/data-factory/.env")
job = uuid.uuid4().hex.upper()

dispatch = {
    "job_id": job,
    "source": {
        "kind": "hana_virtual_table",
        "connection": {
            "host": cfg["IH_HANA_HOST"], "port": int(cfg["IH_HANA_PORT"]),
            "user": cfg["IH_HANA_USER"], "schema": cfg["IH_HANA_SCHEMA"],
            "password": cfg["IH_HANA_PASSWORD"],
            "encrypt": True, "validate_cert": False,
        },
        "virtual_table": cfg["IH_HANA_SOURCE_VT"],
    },
    "rules": json.load(open("mara_rulepack.json")),
    # pointing nowhere on purpose: proves a failed ack does not fail the job
    "callback": {"url": "http://127.0.0.1:9/df-callback", "job_id": job},
}

assert requests.post(f"{DF}/api/v1/data-migration/execute",
                     json=dispatch, timeout=900).status_code == 202
while True:
    time.sleep(5)
    s = requests.get(f"{DF}/api/v1/data-migration/jobs/{job}", timeout=120).json()
    print(f"{s['status']:<10} {s['progressPct']}%  read={s['rowsRead']} "
          f"passed={s['rowsPassed']} written={s['rowsWritten']}")
    if s["status"] in ("COMPLETED", "FAILED"):
        print(json.dumps({k: v for k, v in s.items() if k != "violations"}, indent=1))
        break
```

### A measured run (2026-08-26, tag `solace-1247`, real `STAGING_VT_MARA`)

| | |
|---|---|
| rules | 120 |
| rowsRead | 32,843 |
| rowsPassed / rowsWritten | 3,448 / 3,448 |
| rulesViolated | 53 |
| errorRows | 101,780 |
| credentialSource | `dispatch` |
| wallclock | **14.9 s** warm, 20.8 s on the first job after a restart |

### Why the first job after a restart is slower

Three fixed costs used to be paid by every job, and two of them are now paid once:

| cost | was | now |
|---|---|---|
| connect + TLS + auth | 4.2 s every job | once, then pooled per `(host, port, user, …)` |
| `SELECT * LIMIT 0` | 3–5 s every job | cached 5 min per `(host, user, table)` |
| scanning the federated source | twice: narrow, then all 241 columns | once, into `DF_SRC_<job>` |

That last one is why WRITING collapsed from ~9.6 s to ~0.6 s: the report join runs
against a local copy instead of crossing to SAP a second time. The copy is dropped when
the job ends, including when it fails.

Rules are not the cost. The same job with **1 rule** and with **120 rules** finished
within 0.3 s of each other — 120× the rule work for ~1% of the runtime.

### Two failure modes worth rehearsing

Both are fast and both name the cause exactly — neither leaves a partial migration:

```
FAILED in 0.16s  credentialSource ""
  RuntimeError: no connection secret available for USR_…@… . Supply one of: …

FAILED in 3.05s  credentialSource "dispatch"
  ValueError: 5 column(s) referenced by rules do not exist in STAGING_VT_MARA: …
```

### If the numbers look catastrophic, check the pack before the code

A single stale `allowed_values` list can zero an entire migration. `MANDT is a known
value` expects `["120"]`; after the source client changed, **0 of 32,843 rows matched**
and `rowsPassed` went to 0 — which reads as a total regression and is nothing of the
kind. `violations` names the rule immediately: look for any rule at 100 % of `rowsRead`
first.

---

## See also

- [`VALIDATION_API.md`](VALIDATION_API.md) — the same rules against a file
- [`RULE_DIFFICULTY_ROUTER.md`](RULE_DIFFICULTY_ROUTER.md) — inline vs workflow routing
- [`df_migration_job.py`](../app/layer2_application/features/data_migration/df_migration_job.py) — "Polars decides; SQL moves the bytes."
