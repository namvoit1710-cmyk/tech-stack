# SMDG Log Worker

A **local** multi-type worker that fetches SMDG logs from two places:

| node type | source | what it does |
|---|---|---|
| `request_action_log` | CommonProcessService OData | OAuth `client_credentials` login, then GET `RequestActionLog` by `reqID` |
| `submit_event_log` | CommonProcessService OData | same, for `SubmitEventLog` |
| `cloud_logging_search` | BTP Cloud Logging (OpenSearch) | generic search: index, time window, field match, or raw DSL |
| `cf_requests_logs_search` | BTP Cloud Logging (OpenSearch) | SAP's "CF Requests and Logs" dashboard by time window + context |

Each node authenticates **inside the handler** — there is no separate HTTP/token node in
the flow. Every endpoint knob is a configurable node field with a sensible default.

## 1. Install

```bash
cd workflow/worker/log-worker
python -m venv .venv && . .venv/Scripts/activate    # Windows; use bin/activate on *nix
pip install -r requirements.txt
```

That installs the SDK editable from `../worker-sdk`. If you'd rather not install,
put the SDK on the path instead:

```bash
export PYTHONPATH=/abs/path/to/workflow/worker/worker-sdk
```

Requires Python ≥ 3.12.

## 2. Secrets

Credentials come from **environment variables, a JSON file, or both** — see
`app/secrets.py`. Layered lowest-to-highest, so either half can come from either source:

| priority | source |
|---|---|
| 3 (wins) | individual env vars — `CLS_USERNAME`, `CLS_PASSWORD`, `CLS_URL`, `LOG_WORKER_CLIENT_ID_<CUSTOMER>_<ENV>` |
| 2 | `SMDG_WORKER_SECRETS_JSON` — the whole JSON document in one env value |
| 1 | the secrets file |

OAuth client credentials are **customer + env scoped** — `LOG_WORKER_CLIENT_ID_<CUSTOMER>_<ENV>`
and `LOG_WORKER_CLIENT_SECRET_<CUSTOMER>_<ENV>`. The get-log nodes pick the set from the
target link, or from an explicit **`cred_env`** node field (`mck_dev` | `mck_qas` |
`alcon_dev` | `alcon_qas` | `s4_qas`):

| set | when | env vars |
|---|---|---|
| `mck_dev`    | default (no `-qas` in the link)     | `LOG_WORKER_CLIENT_ID_MCK_DEV` / `..._SECRET_MCK_DEV` |
| `mck_qas`    | link contains `-qas`                | `LOG_WORKER_CLIENT_ID_MCK_QAS` / `..._SECRET_MCK_QAS` |
| `alcon_dev` | link contains `alcon` (no `-qas`)   | `LOG_WORKER_CLIENT_ID_ALCON_DEV` / `..._SECRET_ALCON_DEV` |
| `alcon_qas` | link contains `alcon` and `-qas`    | `LOG_WORKER_CLIENT_ID_ALCON_QAS` / `..._SECRET_ALCON_QAS` |
| `s4_qas`     | link contains `s4-qas`              | `LOG_WORKER_CLIENT_ID_S4_QAS` / `..._SECRET_S4_QAS` |

> The Alcon host token is **`alcon`** (`alcon-dev-simplemdg…` / `alcon-qas-simplemdg…`).

> The old un-suffixed `LOG_WORKER_CLIENT_ID` / `_SECRET` and `..._QAS` names still work as
> deprecated **MCK** dev/qas aliases, so nothing already deployed breaks.

**Env only** (containers, Cloud Foundry — no file needed):

```bash
export CLS_URL='https://dashboards-<id>.cls-NN.cloud.logs.services.<region>.hana.ondemand.com'
export CLS_USERNAME='wcp_log_reader'
export CLS_PASSWORD='<>= 20 chars>'
export LOG_WORKER_CLIENT_ID_MCK_DEV='<CommonProcessService OAuth client, MCK dev>'
export LOG_WORKER_CLIENT_SECRET_MCK_DEV='<...>'
export LOG_WORKER_CLIENT_ID_ALCON_QAS='<Alcon QAS OAuth client>'
export LOG_WORKER_CLIENT_SECRET_ALCON_QAS='<...>'
```

```bash
# Cloud Foundry — one variable carries everything
cf set-env <app> SMDG_WORKER_SECRETS_JSON '{"cls_username":"...","cls_password":"...","cls_url":"https://..."}'
cf restage <app>
```

**File** (the original path, still supported) — default `~/.smdg-worker-secrets.json`,
override with `LOG_WORKER_SECRETS` or `CLAUDE_WORKER_SECRETS`:

```json
{
  "client_id_mck_dev": "<CommonProcessService OAuth client, MCK dev>",
  "client_secret_mck_dev": "<...>",
  "client_id_mck_qas": "<MCK qas>",
  "client_secret_mck_qas": "<...>",
  "client_id_alcon_qas": "<Alcon qas>",
  "client_secret_alcon_qas": "<...>",

  "cls_url": "https://dashboards-<id>.cls-NN.cloud.logs.services.<region>.hana.ondemand.com",
  "cls_username": "<OpenSearch INTERNAL user>",
  "cls_password": "<>= 20 chars>"
}
```

`client_id_*`/`client_secret_*` serve the two OData nodes (one pair per customer+env; the
old un-suffixed `client_id`/`client_secret` still work as the MCK-dev fallback). `cls_*`
serve the two Cloud Logging nodes.

> **`cls_username` is an OpenSearch *internal* user — NOT the service key's
> `dashboards-username`.** Cloud Logging's `basic_internal_auth_domain` authenticates
> against the internal user DB, so service-key credentials return 401. Create one with
> `PUT _plugins/_security/api/internalusers/<name>` plus a role granting `read` on
> `logs-cfsyslog-*`. Rotate with **PATCH** — a `PUT` replaces the whole document and
> silently wipes the user's roles (symptom: 403, not 401).

## 3. Run

**SERVER mode** (default) — the executor pushes tasks to it:

```bash
export APP_MODE=SERVER
export SERVER_PORT=36010
python main.py            # -> Uvicorn running on http://localhost:36010
```

**PULL mode** — the worker leases tasks from a remote executor; nothing needs to reach
your machine, which is why this worker is usable from a laptop:

```bash
export APP_MODE=PULL
export REGISTRY_URL=https://<worker-executor-host>
export WORKER_TENANT=            # blank = the un-partitioned namespace
python main.py
```

PULL uses the in-worker multi-type lease loop (`app/pull_multi.py`) because the stock SDK
pull runner leases exactly one `WORKER_TYPE`. The shared worker-sdk is not modified.

## 4. Check it works

```bash
curl http://localhost:36010/health
curl http://localhost:36010/api/v1/info/types      # expect 4 node types

curl -X POST http://localhost:36010/api/v1/execute \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"t1","worker_type":"cf_requests_logs_search","action":"execute",
       "inputs":{"context":"application","time_from":"now-30m","time_to":"now","size":3}}'
```

## 5. Contracts worth knowing before you wire this into a flow

**A failed fetch still reports the task as SUCCESS.** Handlers never raise; they return
`{"status": "FAILED", "error": ...}` so the flow keeps flowing. A 401 (rotated password),
404, or network blip therefore renders as a **green node**. Every downstream node MUST
gate on `outputs.status` / `outputs.error` — nothing in the platform does it for you.

**Cloud Logging retention defaults to 7 days** (configurable 1–90). A window older than
retention returns empty, not an error.

**The dashboard speaks KQL; `_search` speaks Lucene.** `cf_requests_logs_search` sends
`query_string`, so boolean keywords must be uppercase: `level:ERROR AND app_name:"x"`.
Use the `query_dsl` field for anything Lucene can't express.

**The Cloud Logging read route is unsupported by SAP.** They expose no official query
API — see the module docstring in `app/fetch_cls.py` for the measurements and the quotes.
