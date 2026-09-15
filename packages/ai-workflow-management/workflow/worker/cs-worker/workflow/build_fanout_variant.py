"""Build a FAN-OUT variant of the CS log flow, plus a dispatcher that calls it.

    python build_fanout_variant.py [--apply]

Both are created as DRAFT and with `[TEST]` names. The published pair is never touched —
the point is to measure the two shapes side by side, not to swap one in and hope.

The variant is the published flow with its EDGES rewired and nothing else changed: same
nodes, same parameters, same prompts. Any timing difference is therefore the shape, not a
config drift.

Shape (the one the original MCK-QAS flow uses):

    trigger ─┬─ request_log ──────────────┐
             ├─ req_action_log ───────────┤
             └─ submit_event_log ─┬───────┤        join_logs ─┬─ upload_log → parse_upload ─┐
                                  ├ abap_log ─────┤                                          ├ join_final → out
                                  └ cls_search ───┘           └─ claude_summary ─────────────┘

One edge per branch into `join_logs` — a MERGE with strategy=all waits per incoming EDGE,
not per in-port, so wiring a node's `failure` port into the same merge port would make it
wait for a branch that never arrives. That mistake cost a stalled run the first time
(`7e6bdfb6`); the failure ports of merge-fed nodes stay unwired, which is safe only
because these handlers return errors as DATA and take the success wire.

A dispatcher clone is built too, because the failure this is testing for — the
`expected version N` optimistic-concurrency conflict — only ever appeared on CHILD runs,
with a parent waiting on the flow. Measuring the flow on its own would test the wrong
thing.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
CHAIN_FLOW = "3ad8ac75-f712-409c-b8dd-73b05ee5529f"
CHAIN_DISPATCHER = "98a64446-875f-4002-9f7a-28dfe06dbdb8"
APPLY = "--apply" in sys.argv


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def send(path, body, method="POST"):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:600]}


def port(pid, label, required=True):
    return {"id": pid, "label": label, "data_type": "any",
            "required": required, "description": "", "readonly": False}


PORT_IN = [port("in_default", "Input")]
PORT_OUT = [port("out_default", "Output")]


def edge(src, sport, dst, dport="in_default"):
    return {"id": f"e_{src}_{sport}__{dst}_{dport}", "source_node_id": src,
            "target_node_id": dst, "condition": None, "source_port_id": sport,
            "target_port_id": dport, "label": ""}


# ---------------------------------------------------------------- the flow
src = get(f"/api/v1/workflows/{CHAIN_FLOW}")
nodes = json.loads(json.dumps(src["nodes"]))
by_id = {n["id"]: n for n in nodes}

# The chain has no join_logs (it was removed when the fetches were serialised). Rebuild it.
join_logs = {
    "id": "join_logs", "name": "6. Wait for all logs", "node_type": "MERGE",
    "worker_type": "builtin:merge",
    "parameters": {"merge_strategy": "all", "timeout_seconds": 900,
                   "on_branch_failure": "fail"},
    "x": 1320, "y": 60, "node_class": "", "description": "", "instruction": "",
    "icon": "", "color": "#3b82f6", "tags": [],
    "ports": {"in": PORT_IN + [port(f"in_{n}", f"In: {n}", required=False)
                               for n in ("request_log", "req_action_log",
                                         "abap_log", "cls_search")],
              "out": PORT_OUT},
    "node_definition_id": None, "editable": True,
}
nodes.append(join_logs)

# Lay the fan out vertically so the canvas shows what runs together.
for nid, (x, y) in {"request_log": (330, -180), "req_action_log": (330, 20),
                    "submit_event_log": (330, 220), "abap_log": (700, 140),
                    "cls_search": (700, 340), "upload_log": (1600, -60),
                    "parse_upload": (1850, -60), "claude_summary": (1600, 200),
                    "join_final": (2100, 60), "flow_out": (2340, 60)}.items():
    if nid in by_id:
        by_id[nid]["x"], by_id[nid]["y"] = x, y

edges = [
    edge("trigger-1", "out_default", "request_log"),
    edge("trigger-1", "out_default", "req_action_log"),
    edge("trigger-1", "out_default", "submit_event_log"),
    # These two need the mdgLogID that submit_event_log produces.
    edge("submit_event_log", "success", "abap_log"),
    edge("submit_event_log", "success", "cls_search"),
    # Exactly one edge per branch into the merge.
    edge("request_log", "success", "join_logs", "in_request_log"),
    edge("req_action_log", "success", "join_logs", "in_req_action_log"),
    edge("abap_log", "success", "join_logs", "in_abap_log"),
    edge("cls_search", "success", "join_logs", "in_cls_search"),
    edge("join_logs", "out_default", "upload_log"),
    edge("join_logs", "out_default", "claude_summary"),
    edge("upload_log", "success", "parse_upload"),
    edge("parse_upload", "success", "join_final", "in_parse_upload"),
    edge("claude_summary", "success", "join_final", "in_claude_summary"),
    edge("join_final", "out_default", "flow_out"),
    # Failure ports that can go somewhere without breaking merge arithmetic.
    edge("submit_event_log", "failure", "abap_log"),
    edge("upload_log", "failure", "parse_upload"),
]

flow_body = {
    "name": "[TEST][LOG][CS] fan-out variant (measuring against 3ad8ac75)",
    "description": ("Fan-out variant of the published CS log flow, for a side-by-side "
                    "measurement of shape only: identical nodes and parameters, edges "
                    "rewired to run the five fetches in parallel. Not for production use "
                    "- the published flow is 3ad8ac75-f712-409c-b8dd-73b05ee5529f."),
    "input_schema": json.loads(json.dumps(src.get("input_schema") or [])),
    "output_schema": [], "nodes": nodes, "edges": edges, "metadata": {},
    "schema_version": "1.0.0", "is_confirm": True,
}

print(f"fan-out flow: nodes={len(nodes)} edges={len(edges)}")
ids = {n["id"] for n in nodes}
bad = [e["id"] for e in edges if e["source_node_id"] not in ids or e["target_node_id"] not in ids]
print("  dangling edges:", bad or "none")
into_join = [e for e in edges if e["target_node_id"] == "join_logs"]
print(f"  edges into join_logs: {len(into_join)} (must equal the 4 branches)")

if not APPLY:
    print("\n[DRY RUN] nothing sent. Re-run with --apply.")
    raise SystemExit(0)

code, res = send("/api/v1/workflows", flow_body)
print("  POST ->", code, "" if code in (200, 201) else json.dumps(res)[:400])
if code not in (200, 201):
    raise SystemExit(1)
FANOUT_FLOW = res["data"]["id"]
print("  >>> FANOUT_FLOW =", FANOUT_FLOW)

# ---------------------------------------------------- the dispatcher that calls it
d = get(f"/api/v1/workflows/{CHAIN_DISPATCHER}")
dnodes = json.loads(json.dumps(d["nodes"]))
for n in dnodes:
    if n["id"] == "call_logs":
        n["parameters"]["workflow_id"] = FANOUT_FLOW

disp_body = {
    "name": "[TEST][BOT] CS dispatcher -> fan-out flow",
    "description": ("Clone of the published CS dispatcher, pointed at the fan-out flow "
                    "variant. Exists because the failure being measured for - the "
                    "'expected version N' conflict - only appeared on CHILD runs."),
    "input_schema": json.loads(json.dumps(d.get("input_schema") or [])),
    "output_schema": [], "nodes": dnodes,
    "edges": json.loads(json.dumps(d["edges"])), "metadata": {},
    "schema_version": "1.0.0", "is_confirm": True,
}
code, res = send("/api/v1/workflows", disp_body)
print("  POST dispatcher ->", code, "" if code in (200, 201) else json.dumps(res)[:400])
if code not in (200, 201):
    raise SystemExit(1)
print("  >>> FANOUT_DISPATCHER =", res["data"]["id"])

with open("fanout_ids.txt", "w") as f:
    f.write(f"{FANOUT_FLOW}\n{res['data']['id']}\n")
