"""Run the CS log flow and report where the time actually went.

    python measure_flow.py [repeats]

Prints per-node wall clock from the execution log (NodeDispatched -> NodeCompleted), which
is what a caller feels — it includes the queue wait, not just the work. The summary line
is the one that matters for the fan-out question: how much of the run is the fetch chain,
and how much of that a parallel shape could recover.
"""

import datetime
import json
import sys
import time
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
LOG_FLOW = "3ad8ac75-f712-409c-b8dd-73b05ee5529f"
FETCHERS = ("request_log", "req_action_log", "submit_event_log", "abap_log", "cls_search")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def parse(t):
    try:
        return datetime.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def measure(run_id):
    log = get(f"/api/v1/runs/{run_id}/execution-log")
    t0, started, spans = None, {}, []
    for e in log.get("events") or []:
        ts = parse(e.get("created_at") or e.get("timestamp") or e.get("occurred_at"))
        if ts and t0 is None:
            t0 = ts
        nid = (e.get("payload") or {}).get("node_id")
        if not (ts and nid):
            continue
        if e.get("event_type") == "NodeDispatched" and nid not in started:
            started[nid] = ts
        if e.get("event_type") == "NodeCompleted" and nid in started:
            spans.append((started[nid], (ts - started[nid]).total_seconds(), nid))
    spans.sort()
    return t0, spans


for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
    t_start = time.monotonic()
    rid = post("/api/v1/runs", {
        "workflow_id": LOG_FLOW, "tenant_id": "system",
        "input_data": {"reqID": "CR0000000134", "project": "ALCON", "environment": "DEV"},
    })["id"]
    while True:
        run = get(f"/api/v1/runs/{rid}")
        if str(run.get("status", "")).upper() in ("COMPLETED", "FAILED", "ERROR", "CANCELLED"):
            break
        time.sleep(3)
    wall = time.monotonic() - t_start

    t0, spans = measure(rid)
    print(f"\n=== run {rid[:8]}  {run['status']}  {wall:.0f}s wall clock")
    for s, d, nid in spans:
        print(f"    {nid:<18} start +{(s - t0).total_seconds():5.1f}s   took {d:6.1f}s")
    fetch = [d for _, d, n in spans if n in FETCHERS]
    if fetch:
        print(f"    {'':<18} fetch chain (serial): {sum(fetch):.1f}s"
              f"   |  slowest single fetch: {max(fetch):.1f}s"
              f"   |  fan-out would save: {sum(fetch) - max(fetch):.1f}s")
