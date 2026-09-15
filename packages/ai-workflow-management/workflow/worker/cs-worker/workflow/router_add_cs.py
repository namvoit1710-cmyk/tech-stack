"""Add a `cs` branch to the SHARED bot router (105b6ad5), pointing at the CS dispatcher.

    python router_add_cs.py                 # dry run — prints the additive diff
    python router_add_cs.py --apply         # PUT the draft (does NOT publish)
    python router_add_cs.py --apply --publish   # ...and publish it

Additive: the smdg / mck / alcon branches are not touched.

Three things this router is unforgiving about, all learned the hard way:

1. **Re-read the live copy immediately before the PUT.** A PUT replaces the WHOLE
   definition, and other people edit these workflows. Sending a definition fetched
   minutes ago silently reverts their work. The script also refuses to write if the
   version moved between the snapshot and the write.
2. **The `default` port must keep `condition: "default"`.** It is the last net for a
   project the router does not know, and a SWITCH with no matching branch and no default
   edge stalls the run rather than answering.
3. **Publishing cancels in-flight runs.** The router is shared by every project's bot, so
   the publish step first lists RUNNING/PENDING runs and refuses if any are live.

The `cs` branch is DEAD until a Teams bot whose relay slug is `cs` exists — the router
switches on the bot slug the relay sends, not on anything in the message. Until then the
path is exercised by running the router directly with `project=cs`, which is how the
ALCON branch was proven before its bot was installed.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
ROUTER = "105b6ad5-d29a-43dc-8ddc-ada23983eff9"

_ID_FILE = "cs_dispatcher_id.txt"
CS_DISPATCHER = (open(_ID_FILE).read().strip() if os.path.exists(_ID_FILE)
                 else "98a64446-875f-4002-9f7a-28dfe06dbdb8")

APPLY = "--apply" in sys.argv
PUBLISH = "--publish" in sys.argv


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def send(path, body, method):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:800]}


PORT_IN = [{"id": "in_default", "label": "Input", "data_type": "any",
            "required": True, "description": "", "readonly": False}]
PORT_OUT = [{"id": "out_default", "label": "Output", "data_type": "any",
             "required": True, "description": "", "readonly": False}]


def port(pid, label):
    return {"id": pid, "label": label, "data_type": "any",
            "required": True, "description": "", "readonly": False}


def node(nid, name, ntype, wtype, params, x, y, ports):
    return {"id": nid, "name": name, "node_type": ntype, "worker_type": wtype,
            "parameters": params, "x": x, "y": y, "node_class": "", "description": "",
            "instruction": "", "icon": "", "color": "#3b82f6", "tags": [],
            "ports": ports, "node_definition_id": None, "editable": True}


def set_var(nid, expr, x, y):
    return node(nid, nid, "SET_VARIABLE", "builtin:set_variable",
                {"variables": [{"name": "reply", "type": "string",
                                "item_type": "", "value_expression": expr}]},
                x, y, {"in": PORT_IN, "out": PORT_OUT})


w = get(f"/api/v1/workflows/{ROUTER}")
with open("router_snapshot_before_cs.json", "w", encoding="utf-8") as f:
    json.dump(w, f, ensure_ascii=False, indent=1)
print(f"snapshot: router_snapshot_before_cs.json  version={w.get('version')} "
      f"status={w.get('status')}")

nodes = json.loads(json.dumps(w["nodes"]))
edges = json.loads(json.dumps(w["edges"]))

# Reuse call_mck's input_mapping as the template — it already forwards every key a
# dispatcher needs, and copying it means the CS branch cannot drift from the others.
mapping = next(n["parameters"]["input_mapping"] for n in nodes if n["id"] == "call_mck")

have = {n["id"] for n in nodes}
if "call_cs" not in have:
    nodes.append(node("call_cs", "CS dispatcher (any project + environment)", "WORKFLOW",
                      "builtin:workflow",
                      {"workflow_id": CS_DISPATCHER, "wait_for_completion": True,
                       "input_mapping": json.loads(json.dumps(mapping))},
                      560, 1100, {"in": PORT_IN,
                                  "out": [port("success", "Success"),
                                          port("failure", "Failure")]}))
    nodes.append(set_var("say_cs", "{{$call_cs.output.reply}}", 830, 1060))
    nodes.append(set_var("say_cs_failed",
                         "❌ The CS dispatcher failed. "
                         "Check the workflow run history.", 830, 1200))
    print("add nodes: call_cs, say_cs, say_cs_failed")
else:
    print("call_cs already present — updating in place")
    for n in nodes:
        if n["id"] == "call_cs":
            n["parameters"]["workflow_id"] = CS_DISPATCHER

for n in nodes:
    if n["id"] != "route_bot":
        continue
    data = n["parameters"].setdefault("data", [])
    if not any(r.get("branch") == "cs" for r in data):
        data.append({"key": "{{$input.project}}", "operations": "==",
                     "value": "cs", "branch": "cs"})
        print("add case: project == 'cs'")
    outs = n["ports"]["out"]
    if not any(p["id"] == "cs" for p in outs):
        i = next((k for k, p in enumerate(outs) if p["id"] == "default"), len(outs))
        outs.insert(i, port("cs", "cs"))
        print("add out port: cs (before default)")

pairs = {(e["source_node_id"], e["source_port_id"], e["target_node_id"]) for e in edges}
for eid, s, sp, d in (("e_cs", "route_bot", "cs", "call_cs"),
                      ("e_cs_ok", "call_cs", "success", "say_cs"),
                      ("e_cs_fail", "call_cs", "failure", "say_cs_failed"),
                      ("e_cs_o1", "say_cs", "out_default", "out"),
                      ("e_cs_o2", "say_cs_failed", "out_default", "out")):
    if (s, sp, d) not in pairs:
        edges.append({"id": eid, "source_node_id": s, "target_node_id": d,
                      "source_port_id": sp, "target_port_id": "in_default",
                      "condition": None, "label": ""})

# The default port keeps its condition — the last net for an unknown project.
for e in edges:
    if e["source_node_id"] == "route_bot" and e["source_port_id"] == "default":
        e["condition"] = "default"

idset = {n["id"] for n in nodes}
problems = []
for e in edges:
    for k in ("source_node_id", "target_node_id"):
        if e[k] not in idset:
            problems.append(f"edge {e['id']}: {k}={e[k]} does not exist")
wired = {(e["source_node_id"], e["source_port_id"]) for e in edges}
for n in nodes:
    if n["node_type"] == "OUTPUT":
        continue
    for p in (n.get("ports") or {}).get("out") or []:
        if (n["id"], p["id"]) not in wired:
            problems.append(f"unwired out port: {n['id']}.{p['id']}")

rb = next(n for n in nodes if n["id"] == "route_bot")
print("\nroute_bot cases:", [r["value"] for r in rb["parameters"]["data"]])
print("route_bot ports:", [p["id"] for p in rb["ports"]["out"]])
print(f"nodes={len(nodes)} edges={len(edges)}  call_cs -> {CS_DISPATCHER}")
print("audit:", "OK" if not problems else "PROBLEMS")
for p in problems:
    print("  !!", p)

if not APPLY:
    print("\n[DRY RUN] nothing sent. Re-run with --apply.")
    raise SystemExit(0)
if problems:
    raise SystemExit("audit found problems; nothing sent.")

# Last read before the write: if the version moved, someone else just edited it.
now = get(f"/api/v1/workflows/{ROUTER}")
if now.get("version") != w.get("version"):
    raise SystemExit(f"the router was just edited by someone else "
                     f"(version {w.get('version')} -> {now.get('version')}). "
                     f"Stopping; re-run the script.")

code, res = send(f"/api/v1/workflows/{ROUTER}", {
    "name": w["name"], "version": w["version"], "description": w.get("description", ""),
    "input_schema": w.get("input_schema") or [],
    "output_schema": w.get("output_schema") or [],
    "nodes": nodes, "edges": edges, "metadata": w.get("metadata") or {},
    "schema_version": w.get("schema_version", "1.0.0"), "is_confirm": True,
}, "PUT")
print("\nPUT ->", code, "" if code == 200 else json.dumps(res)[:400])
if code != 200:
    raise SystemExit(1)

if not PUBLISH:
    print("Draft saved. The LIVE bots still run the previously published router.")
    print("Add --publish to activate (see the in-flight check below before you do).")
    raise SystemExit(0)

# Publishing cancels in-flight runs, and this router is shared by every project's bot.
# The hard gate is the ROUTER's OWN runs — those are the ones a publish of the router
# takes down, and each one is carrying a real person's Teams message right now.
LIVE = ("RUNNING", "PENDING", "CREATED")


def _live(path):
    d = get(path)
    items = d["items"] if isinstance(d, dict) else d
    return [r for r in items if str(r.get("status", "")).upper() in LIVE]


mine = _live(f"/api/v1/runs?workflow_id={ROUTER}&limit=40")
if mine:
    print(f"\n{len(mine)} run(s) of THIS router are still live — publishing cancels them:")
    for r in mine[:10]:
        print("   ", r["id"][:8], r.get("status"))
    raise SystemExit("not publishing. Re-run when they have finished.")

# The tenant-wide count is REPORTED, not enforced. This control plane always carries a
# few CREATED runs that never start (7 of them on 2026-08-24, belonging to unrelated
# workflows), so gating on them would mean never publishing anything. A number that is
# large or climbing is still a reason to stop and look before publishing.
everyone = _live("/api/v1/runs?limit=60")
print(f"\nthis router has no live run. Tenant-wide live runs, other workflows, NOT "
      f"cancelled by this publish: {len(everyone)}")

code, res = send(f"/api/v1/workflows/publish/{ROUTER}",
                 {"version": res["data"]["version"], "is_confirm": True}, "POST")
print("publish ->", code, (res.get("data") or {}).get("status"))

chk = get(f"/api/v1/workflows/publish/{ROUTER}")
rb = next(n for n in chk["nodes"] if n["id"] == "route_bot")
print("\n--- read back from the server, the PUBLISHED copy ---")
print("cases:", [r["value"] for r in rb["parameters"]["data"]])
print("ports:", [p["id"] for p in rb["ports"]["out"]])
for e in chk["edges"]:
    if e["source_node_id"] == "route_bot":
        c = f"  condition={e.get('condition')!r}" if e.get("condition") else ""
        print(f"   {e['source_port_id']:10} -> {e['target_node_id']}{c}")
cs = next((n for n in chk["nodes"] if n["id"] == "call_cs"), None)
print("call_cs.workflow_id:", cs["parameters"]["workflow_id"] if cs else "MISSING")
