"""A/B the chain against the fan-out, through the dispatcher (i.e. as CHILD runs).

    python ab_test.py [rounds]

Alternates the two shapes so load, control-plane state and time of day fall on both
equally — running all of one then all of the other would let a slow ten minutes land
entirely on one arm.

Reports per-run status and duration, then the numbers that decide it: failure rate, and
the median. The failure being watched for is `expected version N`, an optimistic-
concurrency conflict that only ever appeared on child runs.
"""

import json
import statistics
import sys
import time
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
MESSAGE = "CR0000000134 in ALCON project with DEV environment"

ARMS = [
    ("chain  ", "98a64446-875f-4002-9f7a-28dfe06dbdb8"),
    ("fan-out", "dcc80ef4-e0c5-4ad6-ba9c-f8266cffe583"),
]
TERMINAL = ("COMPLETED", "FAILED", "ERROR", "CANCELLED")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def one(wid):
    t0 = time.monotonic()
    rid = post("/api/v1/runs", {"workflow_id": wid, "tenant_id": "system",
                                "input_data": {"message": MESSAGE, "text": MESSAGE}})["id"]
    while True:
        run = get(f"/api/v1/runs/{rid}")
        if str(run.get("status", "")).upper() in TERMINAL:
            return rid, run["status"], time.monotonic() - t0, str(run.get("error") or "")
        time.sleep(3)


rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
results = {name: [] for name, _ in ARMS}

for r in range(rounds):
    for name, wid in ARMS:
        rid, status, secs, err = one(wid)
        results[name].append((status, secs, err))
        flag = "" if status == "COMPLETED" else "   <-- " + err[:90]
        print(f"  r{r + 1} {name}  {rid[:8]}  {status:<9} {secs:5.0f}s{flag}", flush=True)

print()
for name, _ in ARMS:
    rows = results[name]
    ok = [s for st, s, _ in rows if st == "COMPLETED"]
    bad = [e for st, _, e in rows if st != "COMPLETED"]
    line = f"{name}  {len(ok)}/{len(rows)} completed"
    if ok:
        line += (f"   median {statistics.median(ok):.0f}s"
                 f"   min {min(ok):.0f}s   max {max(ok):.0f}s")
    print(line)
    for e in bad:
        print(f"           failure: {e[:140]}")
