"""SA-2526 — build the "Cancel CR" workflow around cs_cancel_request.

Five nodes, because the node carries its own sentence. It used to be nine: a 4-rule SWITCH
reading `code`/`status`, and a SET_VARIABLE per outcome holding the English. That is what a
conditional string costs in this engine — the expression grammar is pure path substitution
(`{{$source.path}}` and nothing else), so every variant of a sentence has to be its own node
with its own branch to select it. `cs_cancel_request` composes `reply` on every return path
instead, beside the fields it describes and under the unit tests that already exercise them.

Two nodes survive that look removable and are not:

* `say_ok` — OUTPUT must resolve on EVERY path, and on the crash path `$cancel` produced no
  output at all, so each path converges through a SET_VARIABLE onto `$variables.reply`.
  MERGE cannot do this: it waits per EDGE and hangs on the branch not taken.
* `say_crashed` — `cancel.failure` fires on a real crash or a lease expiry, and there the
  node's outputs may be absent, so its reply carries NO expression. Being called from a
  parent makes it MORE necessary, not less: F13 measured that a WORKFLOW node's failure port
  is inert, so a parent cannot catch its child's crash. This is the last place that crash
  can still produce a sentence rather than silence.

Dry-run by default. `--apply` creates it; `--apply --update <id>` replaces one.
"""
import json
import sys

import httpx as _httpx

_C = _httpx.Client(timeout=90, transport=_httpx.HTTPTransport(retries=5))
BASE = ("https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10."
        "hana.ondemand.com")
APPLY = "--apply" in sys.argv

CANCELLED = "CANCELLED"
ACTION_NOT_AVAILABLE = "CANCEL_ACTION_NOT_AVAILABLE"

PORT_IN = [{"id": "in_default", "label": "Input", "data_type": "any",
            "required": True, "description": "", "readonly": False}]
PORT_OUT = [{"id": "out_default", "label": "Output", "data_type": "any",
             "required": True, "description": "", "readonly": False}]


def port(pid, label=None):
    return {"id": pid, "label": label or pid, "data_type": "any",
            "required": True, "description": "", "readonly": False}


TASK_PORTS = {"in": PORT_IN, "out": [port("success", "Success"), port("failure", "Failure")]}


def node(nid, name, ntype, wtype, params, x, y, ports, desc=""):
    return {"id": nid, "name": name, "node_type": ntype, "worker_type": wtype,
            "parameters": params, "x": x, "y": y, "node_class": "", "description": desc,
            "instruction": "", "icon": "", "color": "#3b82f6", "tags": [],
            "ports": ports, "node_definition_id": None, "editable": True}


def say(nid, name, reply, ok, x, y, desc="", ok_type="boolean"):
    """Sets BOTH reply and ok, so the OUTPUT node never reads a source that did not run.

    ``ok`` is declared boolean, not string: bound to ``{{$cancel.ok}}`` it resolves to the
    native value, and a string variable would stringify that as ``"True"`` -- Python's
    casing, leaking out of the worker and into an API response."""
    return node(nid, name, "SET_VARIABLE", "builtin:set_variable",
                {"variables": [
                    {"name": "reply", "type": "string", "item_type": "",
                     "value_expression": reply},
                    {"name": "ok", "type": ok_type, "item_type": "",
                     "value_expression": ok}]},
                x, y, {"in": PORT_IN, "out": PORT_OUT}, desc)


def field(key, label):
    return {"key": key, "outputType": "string",
            "fieldConfig": {"fieldWrapper": "FormItemWrapper",
                            "wrapperProps": {"label": label},
                            "fieldControl": "InputControl"}}


INPUT_SCHEMA = [
    field("reqID", "Change request to cancel (e.g. CR0000031234)"),
    field("project", "Project (MCK, ALCON, S4 …)"),
    field("environment", "Environment (DEV, QAS …)"),
    field("comment", "Cancellation comment, stored on the request"),
]

nodes = [
    node("start", "Start (reqID + project + environment)", "TRIGGER", "builtin:trigger",
         {"trigger_type": "manual"}, 80, 360, {"in": [], "out": PORT_OUT}),

    node("cancel", "Cancel the change request", "TASK", "worker:cs_cancel_request",
         {"reqID": "{{$input.reqID}}", "project": "{{$input.project}}",
          "environment": "{{$input.environment}}", "comment": "{{$input.comment}}"},
         340, 360, TASK_PORTS,
         "RequestorService/cancelRequest on the srv-process-requestor app — the same "
         "action the UI's Cancel button performs. The only mutating node in the CS "
         "worker."),

    say("say_ok", "say_ok", "{{$cancel.reply}}", "{{$cancel.ok}}", 880, 260,
        "Carries the node's own sentence through. The four branches this replaces existed "
        "only to choose between four strings, and the engine charges a node per string: "
        "its expression grammar is pure path substitution, with no conditional. The node "
        "composes the sentence beside the fields it describes, where the unit tests "
        "already cover the paths that produce it."),

    # cancel.failure fires only on a genuine crash/timeout, and then the node's outputs
    # may not exist — so this reply carries NO expression at all.
    say("say_crashed", "say_crashed",
        "❌ The cancel step crashed before it could report anything. Check the "
        "workflow run history.", "false", 880, 480,
        "Static text on purpose: on this port the node's outputs may be absent, and an "
        "unresolvable expression would take the reply down with it. Kept deliberately "
        "when the flow was collapsed — a parent CANNOT catch this (F13: a WORKFLOW node's "
        "failure port is inert), so without this node a crash is silence."),

    node("out", "Output", "OUTPUT", "builtin:output",
         {"output_schema": [
             {"key": "reply", "type": "string", "value": "{{$variables.reply}}",
              "field_id": "cc100001-1111-4111-8111-111111111111"},
             {"key": "ok", "type": "boolean", "value": "{{$variables.ok}}",
              "field_id": "cc100002-2222-4222-8222-222222222222"},
             {"key": "reqID", "type": "string", "value": "{{$input.reqID}}",
              "field_id": "cc100003-3333-4333-8333-333333333333"}]},
         1200, 360, {"in": PORT_IN, "out": PORT_OUT},
         "Only values that resolve on EVERY path: both say_* variables plus the trigger "
         "input. An OUTPUT value that cannot resolve fails the whole node."),
]

EDGES = [
    ("e_start", "start", "out_default", "cancel"),
    ("e_ok", "cancel", "success", "say_ok"),
    ("e_crash", "cancel", "failure", "say_crashed"),
    ("e_o1", "say_ok", "out_default", "out"),
    ("e_o2", "say_crashed", "out_default", "out"),
]
edges = [{"id": eid, "source_node_id": s, "target_node_id": d, "source_port_id": sp,
          "target_port_id": "in_default", "condition": None, "label": ""}
         for eid, s, sp, d in EDGES]

# ------------------------------------------------- local audit, before anything is sent
problems = []
ids = [n["id"] for n in nodes]
if len(set(ids)) != len(ids):
    problems.append("duplicate node ids")
idset = set(ids)
for e in edges:
    for k in ("source_node_id", "target_node_id"):
        if e[k] not in idset:
            problems.append("edge %s: %s=%s missing" % (e["id"], k, e[k]))
declared = {(n["id"], p["id"]) for n in nodes for p in (n["ports"].get("out") or [])}
wired = {(e["source_node_id"], e["source_port_id"]) for e in edges}
for n in nodes:
    if n["node_type"] == "OUTPUT":
        continue
    for p in n["ports"].get("out") or []:
        if (n["id"], p["id"]) not in wired:
            problems.append("dangling out-port: %s.%s" % (n["id"], p["id"]))
for s, sp in wired:
    if (s, sp) not in declared:
        problems.append("edge leaves an undeclared port: %s.%s" % (s, sp))
reached = {e["target_node_id"] for e in edges}
for n in nodes:
    if n["node_type"] != "TRIGGER" and n["id"] not in reached:
        problems.append("unreachable node: %s" % n["id"])
for n in nodes:
    if n["node_type"] == "SWITCH":
        last = (n["parameters"].get("data") or [{}])[-1]
        if "{{$workflow.id}}" not in str(last.get("key")):
            problems.append("SWITCH %s has no catch-all as its LAST rule" % n["id"])
OK_NON_ASCII = set("✅❌⚠️ℹ️❓—…")
blob = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
odd = sorted({c for c in blob if ord(c) > 127} - OK_NON_ASCII)
if odd:
    problems.append("non-ASCII in the definition (config must be English): %r" % odd)

print("audit:", "OK" if not problems else "PROBLEMS")
for p in problems:
    print("  !!", p)
print("\nnodes=%d edges=%d" % (len(nodes), len(edges)))
print("shape:", " ".join("%s(%s)" % (n["id"], n["node_type"]) for n in nodes))
print("input_schema:", [f["key"] for f in INPUT_SCHEMA])

body = {
    "name": "[CS] Cancel CR (RequestorService)",
    "description": (
        "Cancels a change request through RequestorService/cancelRequest on the "
        "srv-process-requestor app — the same action the UI's Cancel button performs — "
        "using the cs-worker's project registry for the endpoint and the OAuth client. "
        "Works on every landscape checked, with a plain client_credentials token. Every "
        "outcome — cancelled, refused with the backend's own code, action not offered by "
        "this landscape, fault — ends in one sentence plus ok=true|false. SA-2526."),
    "input_schema": INPUT_SCHEMA, "output_schema": [],
    "nodes": nodes, "edges": edges, "metadata": {},
    "schema_version": "1.0.0", "is_confirm": True,
}
with open("cancel_flow_body.json", "w", encoding="utf-8") as f:
    json.dump(body, f, ensure_ascii=False, indent=1)
print("\nwrote cancel_flow_body.json")

# --update <id> replaces an existing definition. Without it a second --apply creates a
# SECOND workflow with the same name, which is how this script produced a duplicate once.
UPDATE = ""
if "--update" in sys.argv:
    UPDATE = sys.argv[sys.argv.index("--update") + 1]

if not APPLY:
    print("\n[DRY RUN] nothing sent. --apply to create it (as a DRAFT), "
          "or --apply --update <id> to replace one.")
    raise SystemExit(0)
if problems:
    raise SystemExit("audit failed above, not sending.")

if UPDATE:
    cur = _C.get(BASE + "/api/v1/workflows/" + UPDATE).json()["data"]
    print("\nupdating %s (version %s, status %s)"
          % (UPDATE, (cur.get("version") or "")[:8], cur.get("status")))
    put = _C.put(BASE + "/api/v1/workflows/" + UPDATE,
                 json={**body, "version": cur["version"]})
    print("PUT ->", put.status_code,
          "" if put.status_code == 200 else (put.text or "")[:400])
    put.raise_for_status()
    back = _C.get(BASE + "/api/v1/workflows/" + UPDATE).json()["data"]
    print("re-read: version=%s status=%s nodes=%d"
          % ((back.get("version") or "")[:8], back.get("status"), len(back["nodes"])))
    raise SystemExit(0)

r = _C.post(BASE + "/api/v1/workflows", json=body)
print("\nPOST ->", r.status_code, "" if r.status_code in (200, 201) else (r.text or "")[:600])
r.raise_for_status()
data = r.json()["data"]
print(">>> CANCEL_FLOW =", data["id"], " status =", data.get("status"))
with open("cancel_flow_id.txt", "w") as f:
    f.write(data["id"])
