"""Every branch of the cancel flow, through the real engine, against tests/stub_backend.py.

Project TEST / environment LOCAL resolves to the stub, so no real change request is
touched. Start the stub and a one-node worker first:

    python tests/stub_backend.py &
    CS_STUB=1 python tests/local_one_node.py cs_cancel_request &
    python workflow/smoke_cancel_flow.py
"""
import json
import sys
import time

import httpx

C = httpx.Client(timeout=90, transport=httpx.HTTPTransport(retries=3))
BASE = ("https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10."
        "hana.ondemand.com")
FLOW = "26df8687-df15-499b-a1b0-95e05cb1e8b5"

# label, reqID, a marker that must appear in the reply, expected ok
# Every case now runs the SAME nodes -- start, cancel, say_ok, out -- because the sentence
# is chosen inside the worker. So the assertion is on the sentence, not on the path.
CASES = [
    ("cancelled",                    "CR_OK",       "has been cancelled",  "true"),
    ("refused (CAN_NOT_LOCK)",       "CR_LOCK",     "CAN_NOT_LOCK",        "false"),
    ("landscape has no such action", "CR_NOACTION", "web app",             "false"),
    ("401 -> a real fault",          "CR_ANYTHING", "did not go through",  "false"),
]

results = []
for label, rid, want_text, want_ok in CASES:
    r = C.post(BASE + "/api/v1/runs", json={
        "workflow_id": FLOW, "tenant_id": "system",
        "input_data": {"reqID": rid, "project": "TEST", "environment": "LOCAL",
                       "comment": "SA-2526 branch smoke"}})
    r.raise_for_status()
    run = r.json()["data"]["id"]
    st = ""
    for _ in range(150):
        s = C.get(f"{BASE}/api/v1/runs/{run}").json()["data"]
        st = str(s.get("status", "")).upper()
        if st in ("COMPLETED", "FAILED", "ERROR", "CANCELLED"):
            break
        time.sleep(2)
    log = C.get(f"{BASE}/api/v1/runs/{run}/execution-log").json()["data"]
    seen, order = set(), []
    for ev in log.get("events") or []:
        n = (ev.get("payload") or {}).get("node_id")
        if n and n not in seen:
            seen.add(n)
            order.append(n)
    got = {}
    for nid in ("out",):
        o = C.get(f"{BASE}/api/v1/runs/{run}/nodes/{nid}/output").json().get("data") or {}
        for it in o.get("iterations") or []:
            for sc in it.get("scopes") or []:
                for pa in sc.get("paths") or []:
                    if pa.get("path") in ("output.reply", "output.ok"):
                        got[nid + "." + pa["path"]] = pa.get("sample")
    reply = str(got.get("out.output.reply") or "")
    raw_ok = got.get("out.output.ok")
    ok = (st == "COMPLETED"
          and "say_ok" in seen and "say_crashed" not in seen
          and want_text in reply
          and str(raw_ok).lower() == want_ok)
    print("\n" + "=" * 74)
    print("CASE %-32s reqID=%s" % (label, rid))
    print("  run %s -> %s" % (run, st))
    print("  ran   : %s" % " -> ".join(order[1:]))
    print("  ok    : %-6r (%s)  expected %s"
          % (raw_ok, type(raw_ok).__name__, want_ok))
    print("  want  : %r in reply" % want_text)
    print("  reply : %s" % reply[:220])
    print("  VERDICT:", "PASS" if ok else "FAIL")
    results.append((label, ok))

print("\n" + "=" * 74)
print("%d/%d passed" % (sum(1 for _, o in results if o), len(results)))
for label, ok in results:
    print("  %-4s %s" % ("PASS" if ok else "FAIL", label))
