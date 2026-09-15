"""Call the CS workflows over HTTP — start, poll, read the answer.

    python call_cs_api.py log  CR0000000134 ALCON DEV
    python call_cs_api.py chat "CR0000000134 in ALCON project with DEV environment"

Standard library only, so it runs anywhere Python 3.8+ does.

Three things this exists to get right, each of which is easy to get wrong from the API
alone:

1. **`output_data` on the run is a FILE REFERENCE, not the answer.** `GET /runs/{id}`
   returns `{"out": {"__file_ref": true, "file_id": "file_…", "columns": [...]}}`. The
   values live at `GET /runs/{id}/nodes/{node}/output`, under
   `iterations[].scopes[].paths[]` where `path` starts with `output.`.
2. **These runs are slow on purpose.** The analysis is several OData round trips plus a
   Claude call: 45–76 s. A caller that treats this as request/response will time out.
   Poll, or run it as a background job.
3. **A FAILED run still answers.** `status` and `error` on the run tell you what broke.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"

LOG_FLOW = "3ad8ac75-f712-409c-b8dd-73b05ee5529f"     # structured: reqID + project + env
DISPATCHER = "98a64446-875f-4002-9f7a-28dfe06dbdb8"   # free text, with the gate

# Which node carries the answer. Not guessable from the API — it is the OUTPUT node's id
# in each workflow definition.
OUTPUT_NODE = {LOG_FLOW: "flow_out", DISPATCHER: "out"}

TERMINAL = ("COMPLETED", "FAILED", "ERROR", "CANCELLED")


def _get(path):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def start(workflow_id, input_data, correlation_id=""):
    """POST /api/v1/runs -> run id. Only workflow_id is required; tenant_id defaults to
    "system". correlation_id is yours to set — it comes back on the run, which is how you
    tie a run to your own request without keeping a map."""
    body = {"workflow_id": workflow_id, "tenant_id": "system", "input_data": input_data}
    if correlation_id:
        body["correlation_id"] = correlation_id
    return _post("/api/v1/runs", body)["id"]


def wait(run_id, timeout_s=300, interval_s=4):
    """Poll until the run reaches a terminal state. Returns the run object."""
    deadline = time.monotonic() + timeout_s
    while True:
        run = _get(f"/api/v1/runs/{run_id}")
        if str(run.get("status", "")).upper() in TERMINAL:
            return run
        if time.monotonic() > deadline:
            raise TimeoutError(f"run {run_id} still {run.get('status')} after {timeout_s}s")
        time.sleep(interval_s)


def outputs(run_id, node_id):
    """The node's real output values as a flat dict: {"reply": "...", "decision": "run"}.

    Walks iterations -> scopes -> paths and keeps the `output.*` ones. Later iterations
    win, which matters only for nodes inside a loop; these workflows have none."""
    try:
        data = _get(f"/api/v1/runs/{run_id}/nodes/{node_id}/output")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}          # the node never ran — a stopped branch, for instance
        raise
    out = {}
    for it in data.get("iterations") or []:
        for sc in it.get("scopes") or []:
            for pa in sc.get("paths") or []:
                path = pa.get("path") or ""
                if path.startswith("output."):
                    out[path[len("output."):]] = pa.get("sample")
    return out


def run_and_read(workflow_id, input_data, correlation_id="", **kw):
    """The whole thing: start, wait, read. Returns (run, outputs)."""
    run_id = start(workflow_id, input_data, correlation_id)
    run = wait(run_id, **kw)
    return run, outputs(run_id, OUTPUT_NODE[workflow_id])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "log"
    t0 = time.monotonic()

    if mode == "log":
        req_id, project, env = sys.argv[2], sys.argv[3], sys.argv[4]
        wf, payload = LOG_FLOW, {"reqID": req_id, "project": project, "environment": env}
    elif mode == "chat":
        message = sys.argv[2]
        wf, payload = DISPATCHER, {"message": message, "text": message}
    else:
        raise SystemExit("usage: call_cs_api.py [log <CR> <PROJECT> <ENV> | chat <message>]")

    print(f"POST /api/v1/runs  {json.dumps(payload, ensure_ascii=False)}")
    run, out = run_and_read(wf, payload, correlation_id="cli-demo")
    print(f"  {run['status']} in {time.monotonic() - t0:.0f}s"
          + (f"  error={run.get('error')}" if run.get("error") else ""))
    for k, v in out.items():
        text = str(v)
        print(f"  {k:12} = {text[:400]}{'…' if len(text) > 400 else ''}")
