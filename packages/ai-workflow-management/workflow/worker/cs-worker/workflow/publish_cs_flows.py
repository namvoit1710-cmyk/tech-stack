"""Publish the two CS workflows — dry run unless --apply.

Publishing freezes what callers get, which is the point: an API caller building on a
DRAFT is building on something anyone can edit underneath them.

It also CANCELS that workflow's in-flight runs, so each one is gated on its own live
runs. The child (the log flow) goes first: the dispatcher calls it, and publishing the
callee first means there is never a moment where a published parent points at something
that just changed.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
APPLY = "--apply" in sys.argv

# Child first, then the parent that calls it.
TARGETS = [
    ("3ad8ac75-f712-409c-b8dd-73b05ee5529f", "[LOG][CS] log flow"),
    ("98a64446-875f-4002-9f7a-28dfe06dbdb8", "[BOT] CS dispatcher"),
]

LIVE = ("RUNNING", "PENDING", "CREATED")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:600]}


for wid, label in TARGETS:
    wf = get(f"/api/v1/workflows/{wid}")
    runs = get(f"/api/v1/runs?workflow_id={wid}&limit=40")
    items = runs["items"] if isinstance(runs, dict) else runs
    live = [r for r in items if str(r.get("status", "")).upper() in LIVE]

    print(f"== {label}")
    print(f"   {wid}")
    print(f"   status={wf.get('status')}  version={wf.get('version')}")
    print(f"   nodes={len(wf['nodes'])} edges={len(wf['edges'])}")
    print(f"   live runs of THIS workflow: {len(live)}"
          + ("".join(f"\n     {r['id'][:8]} {r.get('status')}" for r in live[:8]) if live else ""))

    if not APPLY:
        print("   [DRY RUN] not published.\n")
        continue
    if live:
        print("   SKIPPED — publishing would cancel the runs above. Re-run when they finish.\n")
        continue
    if str(wf.get("status")) == "PUBLISHED":
        print("   already PUBLISHED — nothing to do.\n")
        continue

    code, res = post(f"/api/v1/workflows/publish/{wid}",
                     {"version": wf.get("version"), "is_confirm": True})
    print(f"   publish -> {code} "
          + ("" if code in (200, 201) else json.dumps(res)[:400]))
    if code not in (200, 201):
        raise SystemExit(1)

    # Read the PUBLISHED copy back from the server — not the object we just sent.
    time.sleep(1)
    pub = get(f"/api/v1/workflows/publish/{wid}")
    print(f"   read back (published): status={pub.get('status')} "
          f"nodes={len(pub['nodes'])} edges={len(pub['edges'])} version={pub.get('version')}\n")
