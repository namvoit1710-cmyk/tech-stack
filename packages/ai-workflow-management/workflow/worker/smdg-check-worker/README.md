# SMDG Check Worker

A **local** multi-type worker that authenticates to SMDG backend services and reads/checks
MDG data for a change request, an account, or an object. Three node types:

| node type | endpoint | what it does |
|---|---|---|
| `qc_duplication_check` | `CommonProcessService/RequestEventData` | GET by `reqID`, parse the nested `templateConfigData.templateData.DuplicationCheck` / `DuplicationCheckWithCondition` → readable duplicate-config message |
| `qc_scope_auth` | `AdminUserService/UserScopeAuthObjects` | GET an account's scope authorizations (`scopeID` / `authObjectID` / `authObjectValue`), active-only by default |
| `qc_onprem_data` | `MainService/getObjectOnPremiseData` | POST `{objectType, onPremiseObjectID, isConvertToFlat}` → the object's full on-premise data |

## Credentials — from the flow environment, not this worker

There are **no credentials on this worker**. Each node resolves its OAuth credential from
the flow's environment/Section: bind the node's **`credential_ref`** field to an `$env`
credential variable (e.g. `{{$env.OAUTH}}`). The endpoint base URLs are node fields too,
typically bound to `{{$env.*_BASE_API_URL}}` (`PROCESS_BASE_API_URL`,
`ADMIN_USER_BASE_API_URL`, `CONFIG_MAIN_BASE_API_URL`). So there is nothing secret in
`.env` — it holds only run config.

## Run

Requires Python ≥ 3.12. The SDK is on the path from the sibling `worker-sdk`.

```bash
cd workflow/worker/smdg-check-worker
# PULL mode — lease tasks from the remote executor (nothing needs to reach your machine)
export APP_MODE=PULL
export REGISTRY_URL=https://smdg-ai-tenant-1-ai-workflow-worker-executor.cfapps.br10.hana.ondemand.com
export WORKER_TENANT=            # blank = un-partitioned namespace
PYTHONPATH=../worker-sdk python main.py
```

(or copy `.env.example` → `.env` and just `PYTHONPATH=../worker-sdk python main.py`).

Expect: `Pull worker running (multi-type, leasing 3 node types: qc_duplication_check, qc_scope_auth, qc_onprem_data)`.

## Contract worth knowing

A failed fetch still reports the task as **SUCCESS** with `status="FAILED"` + `error` in
the output (handlers never raise) so the flow keeps flowing — gate downstream nodes on
`outputs.status` / `outputs.error`.
