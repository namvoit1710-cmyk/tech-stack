"""Run a CS workflow for real and report which nodes ran and what came out.

    python smoke_cs.py <workflow-id> key=value [key=value ...]

Reports the node ORDER, not just the final status — a green run that skipped the gate is
not the same run as a green run that went through it, and only the order tells them apart.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:800]}


wid = sys.argv[1]
inputs = {}
for arg in sys.argv[2:]:
    if "=" in arg:
        k, _, v = arg.partition("=")
        inputs[k] = v

code, res = post("/api/v1/runs", {"workflow_id": wid, "tenant_id": "system",
                                  "input_data": inputs})
if code not in (200, 201):
    print("POST /runs ->", code, json.dumps(res)[:600])
    raise SystemExit(1)
rid = res["data"]["id"]
print(f"run {rid}  inputs={inputs}")

t0 = time.time()
status = ""
for _ in range(300):
    s = get(f"/api/v1/runs/{rid}")["data"]
    status = str(s.get("status", "")).upper()
    if status in ("COMPLETED", "FAILED", "ERROR", "CANCELLED"):
        break
    time.sleep(2)
print(f"  finished: {status}  in {time.time() - t0:.0f}s  error={str(s.get('error'))[:200]}")

log = get(f"/api/v1/runs/{rid}/execution-log")["data"]
seen, order = set(), []
for e in log.get("events") or []:
    n = (e.get("payload") or {}).get("node_id")
    if n and n not in seen:
        seen.add(n)
        order.append(n)
print("  nodes, in order:", " -> ".join(order))

# The run's own output_data holds a FILE REFERENCE, not the value; the value is under
# /runs/{id}/nodes/{node}/output -> paths[].sample.
for node in sys.argv[1:2] and (order[-3:] if order else []):
    try:
        o = get(f"/api/v1/runs/{rid}/nodes/{node}/output").get("data") or {}
    except Exception as exc:  # noqa: BLE001
        print(f"  {node}: (no output) {exc}")
        continue
    for it in o.get("iterations") or []:
        for sc in it.get("scopes") or []:
            for pa in sc.get("paths") or []:
                if pa.get("path", "").startswith("output."):
                    print(f"  {node}.{pa['path'][:34]:34} = {str(pa.get('sample'))[:400]!r}")
