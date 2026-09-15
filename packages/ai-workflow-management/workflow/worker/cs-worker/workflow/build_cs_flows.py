"""Build the two CS workflows on tenant-1 — dry run unless --apply.

    [LOG][CS] flow        one flow for EVERY project+environment (cs-worker resolves the
                          endpoint and the credentials from project+environment)
    [BOT] CS dispatcher   message -> cs_detect_request gate -> call the flow -> Teams reply

The log flow is a clone of the published MCK-QAS one (`abb79d7a`) with three changes:
  * worker types become the dynamic `cs_*` ones,
  * `api_url` / `token_url` on each node are REPLACED by `project` + `environment`,
  * the deep-analysis branch (claude_deep -> upload_deep -> parse_deep) is dropped, as
    the operator asked — that branch is the second LLM call and the second upload.

Everything written into a workflow definition is English (finding F17): node names, the
description, input-schema labels, and the comments inside `worker:code` nodes.

Both are created as DRAFT. Publishing is a separate, deliberate step — it cancels every
RUNNING/PENDING run on the control plane (finding F3), so it is not something a build
script should do on its own.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# Which workflows this script owns is remembered in two files next to THIS file -- never
# relative to the cwd. Opening them by bare name meant running from the parent directory
# silently created a second dispatcher instead of updating the real one.
STATE_DIR = os.path.dirname(os.path.abspath(__file__))


def state_path(name):
    return os.path.join(STATE_DIR, name)

BASE = "https://smdg-ai-tenant-1-ai-workflow-control-plane.cfapps.br10.hana.ondemand.com"
SRC_FLOW = "abb79d7a-0f1c-4141-bd40-3e39296b0cce"   # v6 [LOG][MCK][QAS][No ticket]
APPLY = "--apply" in sys.argv
# --dispatcher-only: skip the log flow entirely. Republishing 3ad8ac75 when nothing in it
# changed is not free -- it bumps the version and publishing cancels in-flight runs on a
# workflow the CS bot is serving from right now.
DISPATCHER_ONLY = "--dispatcher-only" in sys.argv


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def put(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="PUT",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:800]}


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
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


def port(pid, label, required=True):
    return {"id": pid, "label": label, "data_type": "any",
            "required": required, "description": "", "readonly": False}


TASK_PORTS = {"in": PORT_IN, "out": [port("success", "Success"), port("failure", "Failure")]}


def node(nid, name, ntype, wtype, params, x, y, ports):
    return {"id": nid, "name": name, "node_type": ntype, "worker_type": wtype,
            "parameters": params, "x": x, "y": y, "node_class": "", "description": "",
            "instruction": "", "icon": "", "color": "#3b82f6", "tags": [],
            "ports": ports, "node_definition_id": None, "editable": True}


def edge(src, sport, dst, dport="in_default"):
    return {"id": f"e_{src}_{sport}__{dst}_{dport}", "source_node_id": src,
            "target_node_id": dst, "condition": None, "source_port_id": sport,
            "target_port_id": dport, "label": ""}


def field(key, label):
    return {"key": key, "outputType": "string",
            "fieldConfig": {"fieldWrapper": "FormItemWrapper",
                            "wrapperProps": {"label": label},
                            "fieldControl": "InputControl"}}


def audit(name, nodes, edges, allow_unwired=(), unwired_reason="see MERGE_FED"):
    """The same local check the onboard-project-bot hackathon runs before every write:
    duplicate ids, edges into nowhere, an OUT port left unwired, a node nothing calls.
    An unwired port is how an error path ships unhandled — it costs nothing to catch here."""
    problems = []
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        problems.append("duplicate node ids")
    idset = set(ids)
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
    has_in = {e["target_node_id"] for e in edges}
    for n in nodes:
        if n["node_type"] != "TRIGGER" and n["id"] not in has_in:
            problems.append(f"nothing calls: {n['id']}")
    tolerated = [p for p in problems
                 if p.startswith("unwired out port:") and p.endswith(".failure")
                 and p.split()[-1].split(".")[0] in allow_unwired]
    problems = [p for p in problems if p not in tolerated]
    print(f"  audit {name}: " + ("OK" if not problems else "PROBLEMS"))
    for p in problems:
        print("    !!", p)
    for p in tolerated:
        print(f"    ~ tolerated ({unwired_reason}):", p)
    return problems


# ============================================================================
# 1. The log flow — one flow, every project and environment
# ============================================================================

src = get(f"/api/v1/workflows/{SRC_FLOW}")
S = {n["id"]: n for n in src["nodes"]}

# The four OData nodes: same reqID binding, but addressed by project + environment
# instead of by a hard-coded host. That single change is what makes one flow enough.
TARGET_PARAMS = {"reqID": "{{$input.reqID}}",
                 "project": "{{$input.project}}",
                 "environment": "{{$input.environment}}"}

log_nodes = [
    node("request_log", "1. RequestLog (what the user sees)", "TASK", "worker:cs_request_log",
         dict(TARGET_PARAMS), 300, 60, TASK_PORTS),
    node("req_action_log", "2. RequestActionLog", "TASK", "worker:cs_request_action_log",
         dict(TARGET_PARAMS), 540, 60, TASK_PORTS),
    node("submit_event_log", "3. SubmitEventLog (validation steps)", "TASK",
         "worker:cs_submit_event_log", dict(TARGET_PARAMS), 780, 60, TASK_PORTS),
    node("abap_log", "4. ABAP Log (MD API log)", "TASK", "worker:cs_abap_log",
         {**TARGET_PARAMS,
          # Match either id: a reqID can have several submit attempts, and the mdgLogID
          # from the FAILED SubmitEventLog row isolates the one that actually failed.
          "filter": "mdgLogID eq '{{$submit_event_log.mdgLogID}}' or reqID eq '{{$input.reqID}}'"},
         1020, 60, TASK_PORTS),
    node("cls_search", "5. Cloud Logging search", "TASK", "worker:cs_cloud_logging_search",
         dict(S["cls_search"]["parameters"]), 1260, 60, TASK_PORTS),
]

UPLOAD_BODY = """# Process logs — {{$input.reqID}}

**Project:** {{$input.project}}  **Environment:** {{$input.environment}}

[Open this change request in the web app]({{$request_log.cr_url}})

## What the user sees

HTTP {{$request_log.status_code}}

```
{{$request_log.pretty}}
```

## RequestActionLog — HTTP {{$req_action_log.status_code}} ({{$req_action_log.count}} entries)

```
{{$req_action_log.pretty}}
```

## SubmitEventLog — HTTP {{$submit_event_log.status_code}} ({{$submit_event_log.count}} entries)

```
{{$submit_event_log.pretty}}
```

## ABAP Log — HTTP {{$abap_log.status_code}} ({{$abap_log.count}} entries)

Not every landscape serves this log. When it does not, the line below says so.

```
{{$abap_log.error}}
```

```json
{{$abap_log.response_pretty}}
```

## Cloud Logging (reqID {{$input.reqID}}) — HTTP {{$cls_search.status_code}} ({{$cls_search.count}} hits)

```
{{$cls_search.pretty}}
```
"""

# The join. Four incoming edges from four DISTINCT nodes — submit_event_log is not a
# predecessor here, it does not need to be: abap_log and cls_search are downstream of it,
# so their arrival already proves it completed.
join_fetch = node(
    "join_fetch", "6. Wait for every log fetch", "MERGE", "builtin:merge",
    {"merge_strategy": "all", "timeout_seconds": 900, "on_branch_failure": "fail"},
    780, 60,
    {"in": PORT_IN + [port("in_request_log", "In: request_log", required=False),
                      port("in_req_action_log", "In: req_action_log", required=False),
                      port("in_abap_log", "In: abap_log", required=False),
                      port("in_cls_search", "In: cls_search", required=False)],
     "out": PORT_OUT})

upload_log = node(
    "upload_log", "6. Upload the collected logs (.md)", "TASK", "worker:http_request",
    {**S["upload_log"]["parameters"],
     "url": ("https://smdg-ai-tenant-1-file-service.cfapps.br10.hana.ondemand.com"
             "/api/v1/upload?filename=logs-{{$input.project}}-{{$input.environment}}"
             "-{{$input.reqID}}.md"),
     "body": UPLOAD_BODY},
    1500, 60, TASK_PORTS)

parse_upload = node("parse_upload", "7. Read the file id and build the link", "TASK",
                    "worker:code", dict(S["parse_upload"]["parameters"]),
                    1740, 60, TASK_PORTS)

SUMMARY_LOGS = """Analysis request: change request {{$input.reqID}} in project {{$input.project}}, environment {{$input.environment}}.

=== PROCESS LOGS ===
What the user sees (request-level error):
{{$request_log.pretty}}

SubmitEventLog (validation steps):
{{$submit_event_log.pretty}}

RequestActionLog (actions):
{{$req_action_log.pretty}}

ABAP Log (may be unavailable in this landscape — see the error line):
{{$abap_log.pretty}}
{{$abap_log.error}}

Cloud Logging:
{{$cls_search.pretty}}
"""

# The summary instruction. NOT copied from the MCK-QAS flow any more: that one was
# written for a Jira ticket raised by a business analyst, so it opened with "You help a
# Business Analyst (BA)…" and phrased every action as "the BA must…". This flow has no
# ticket and the reader is the customer-service team, so the answers came back addressing
# somebody who was not in the room. The team mapping below is kept verbatim — it is tuned
# knowledge about which service owns which validation step.
SUMMARY_PROMPT = (
    "You help the customer-service team understand a Change Request (CR) that failed in "
    "the SMDG submit/validation process. You are given (a) which CR, project and "
    "environment was asked about, and (b) the collected process-step logs (SubmitEventLog "
    "validation steps PASSED/FAILED + RequestActionLog actions + ABAP MDApiLog + Cloud "
    "Logging). Write in ENGLISH. The only roles you may name are the NodeJS team and the "
    "ABAP team; do not address anyone in particular and do not invent a role.\n"
    "Output PLAIN TEXT ONLY (no markdown, no asterisks, no backticks). Output EXACTLY "
    "these three sections and NOTHING ELSE (an 'Attachment:' section with file links is "
    "appended automatically by the workflow — do NOT write it yourself, do NOT invent "
    "any URLs):\n"
    "Requirement: <one short paragraph stating which CR, project and environment was "
    "asked about, and what was being investigated>\n"
    "Analysis summary: <a concise result of the analysis: which step blocked the CR (or "
    "that all passed), and the concrete data/error reason from the logs — a few lines "
    "max>\n"
    "Suggested action: <the next action, WITH CONTEXT (why + how) — never just "
    "'contact team X'. If the fix is data in the request itself, say EXACTLY what: e.g. "
    "'field <name> is EMPTY, add value <...> because <reason>' or 'field <name> currently "
    "has <wrong value>, change it to <...> because <reason>'. If it must be escalated, say "
    "which team+service, WHAT to send them (reqID, and the mdgLogID for validateTestrun), "
    "and WHY. Write actions impersonally — 'Correct field X …', never naming a "
    "person or a role that is not the NodeJS or ABAP team.>\n"
    "Team mapping for genuine SYSTEM errors (not data fixes): validateTestrun -> ABAP team "
    "(process service, send the mdgLogID); validateAuthorization -> NodeJS team "
    "(srv-user); validateDuplication -> NodeJS team (process service); "
    "validateParallelChange -> NodeJS team (process-system); validateDataQuality -> NodeJS "
    "team (srv-dqm); validateTemplatePayloadData -> usually a data fix in the request, "
    "only NodeJS (config-system) if it is a genuine system error. A missing/invalid field, "
    "duplicate partner, data-quality rejection, or any validateTemplatePayloadData failure "
    "is a data fix in the request — do NOT route it to a team."
)

# The flag. `summarize=false` skips the Claude summary and answers with the collected
# logs alone - about 50 s instead of 75 s, and no model call at all.
#
# Two rules, and the ORDER is the whole mechanism. Rule 1 reads an input the caller may
# not have sent; when it is absent the resolver raises, the switch handler skips that
# rule and moves on. Rule 2 compares a source that always exists against a value it can
# never equal, so it always matches - that is what makes "run the summary" the default.
# The `default` PORT is not usable for this: the engine only honours a default edge that
# carries the literal condition "default", and the canvas writes `condition: null` on
# every edge it saves, so a default drawn in the UI is dead. Measured, not assumed.
gate_summary = node(
    "gate_summary", "8. Was the summary asked for?", "SWITCH", "builtin:switch",
    {"data": [{"key": "{{$input.summarize}}", "operations": "==", "value": "false",
               "branch": "skip", "port_id": "skip"},
              {"key": "{{$workflow.id}}", "operations": "!=", "value": "__never__",
               "branch": "run", "port_id": "run"}]},
    1980, 60,
    {"in": PORT_IN, "out": [port("skip", "Skip the summary"),
                            port("run", "Run the summary")]})

claude_summary = node(
    "claude_summary", "9. Summarize the logs (Claude)", "TASK",
    "worker:cs_summarize_log",
    {"logs": SUMMARY_LOGS,
     "system_prompt": SUMMARY_PROMPT,
     "timeout_secs": 240},
    2220, 60, TASK_PORTS)

flow_out = node(
    "flow_out", "Flow output", "OUTPUT", "builtin:output",
    {"output_schema": [
        {"key": "summary", "type": "string",
         "value": ("{{$claude_summary.summary}}\n\nAttachment:\n"
                   "- All related logs: {{$parse_upload.result.download_url}}\n"
                   "- Open the change request: {{$request_log.cr_url}}"),
         "field_id": "ccccccc1-1111-4111-8111-111111111111"},
        {"key": "logs_url", "type": "string",
         "value": "{{$parse_upload.result.download_url}}",
         "field_id": "ccccccc2-2222-4222-8222-222222222222"},
        {"key": "reqID", "type": "string", "value": "{{$input.reqID}}",
         "field_id": "ccccccc3-3333-4333-8333-333333333333"},
        {"key": "project", "type": "string", "value": "{{$input.project}}",
         "field_id": "ccccccc4-4444-4444-8444-444444444444"},
        {"key": "environment", "type": "string", "value": "{{$input.environment}}",
         "field_id": "ccccccc5-5555-4555-8555-555555555555"},
        # Resolved by the worker from the target's registry entry, so a caller gets the
        # link without knowing the landscape. Empty for a landscape with no web app.
        {"key": "cr_url", "type": "string", "value": "{{$request_log.cr_url}}",
         "field_id": "ccccccc6-6666-4666-8666-666666666666"},
    ]},
    2460, 60, {"in": PORT_IN, "out": PORT_OUT})

flow_out_plain = node(
    "flow_out_plain", "Flow output (no summary)", "OUTPUT", "builtin:output",
    {"output_schema": [
        {"key": "summary", "type": "string",
         "value": ("Summary skipped (summarize=false).\n\nAttachment:\n"
                   "- All related logs: {{$parse_upload.result.download_url}}\n"
                   "- Open the change request: {{$request_log.cr_url}}"),
         "field_id": "ddddddd1-1111-4111-8111-111111111111"},
        {"key": "logs_url", "type": "string",
         "value": "{{$parse_upload.result.download_url}}",
         "field_id": "ddddddd2-2222-4222-8222-222222222222"},
        {"key": "reqID", "type": "string", "value": "{{$input.reqID}}",
         "field_id": "ddddddd3-3333-4333-8333-333333333333"},
        {"key": "project", "type": "string", "value": "{{$input.project}}",
         "field_id": "ddddddd4-4444-4444-8444-444444444444"},
        {"key": "environment", "type": "string", "value": "{{$input.environment}}",
         "field_id": "ddddddd5-5555-4555-8555-555555555555"},
        # Resolved by the worker from the target's registry entry, so a caller gets the
        # link without knowing the landscape. Empty for a landscape with no web app.
        {"key": "cr_url", "type": "string", "value": "{{$request_log.cr_url}}",
         "field_id": "ddddddd6-6666-4666-8666-666666666666"},
    ]},
    2460, 280, {"in": PORT_IN, "out": PORT_OUT})

trigger = node("trigger-1", "Start (project + environment + CR)", "TRIGGER",
               "builtin:trigger", {"trigger_type": "manual"}, 60, 60,
               {"in": [], "out": PORT_OUT})

flow_nodes = [trigger] + log_nodes + [join_fetch, upload_log, parse_upload,
                                      gate_summary,
                                      claude_summary, flow_out, flow_out_plain]

# Two stages, not a chain — and the shape comes from the data, not from taste.
#
# request_log, req_action_log and submit_event_log read only the run's input, so they
# start together. abap_log and cls_search both interpolate
# {{$submit_event_log.mdgLogID}}, so they cannot start before it lands. Measured on run
# c13ad93e: 4.2 / 4.1 / 3.9 s for the first three, 2.6 and 13.4 s for the last two —
# 27.7 s chained against 17.6 s in two stages. cls_search is the floor and no shape gets
# under it.
#
# This was a chain until 2026-08-28 for a real reason: a fan-out into a MERGE used to hit
# "expected version N" CAS conflicts on the run aggregate whenever a parent was waiting
# on the child (runs 258a9195, 2674c005 on 2026-08-24). That was fixed upstream on
# 2026-08-26 by 2a1671b1b — a per-run write lock plus CAS retry on every aggregate writer
# — and a four-way cs-worker fan-out into one merge now measures 6/6 clean.
#
# Two things about the merge are load-bearing, both learned the hard way:
#   * quorum counts INCOMING EDGES, not in-ports. The 2026-08-24 stall (run 7e6bdfb6)
#     was this flow wiring each fetch's `failure` port into the merge as well, so it
#     waited for 8 arrivals from 4 branches. No failure port is wired into it here.
#   * two edges from ONE source into the merge is a latent never-fires: n_total counts
#     edges while n_done counts distinct predecessor NODES, so such a merge can never
#     reach quorum. Every edge below comes from a different node.
#
# The fetch `failure` ports are deliberately unwired (see MERGE_FED). These handlers do
# not raise — a 401/404/DNS failure comes back as DATA with status=FAILED on the SUCCESS
# wire (finding F16) — so the failure port is decorative. Wiring it anywhere useful would
# either add a second edge from one source into the merge (never fires) or add a fifth
# predecessor that only sometimes runs (waits forever). Unwired is the honest option.
flow_edges = [
    # Stage 1 — everything that needs only the input.
    edge("trigger-1", "out_default", "request_log"),
    edge("trigger-1", "out_default", "req_action_log"),
    edge("trigger-1", "out_default", "submit_event_log"),
    # Stage 2 — everything that needs the mdgLogID. Both edges leave the same node, and
    # that is fine: their target is a TASK, which fires once on whichever edge arrives.
    edge("submit_event_log", "success", "abap_log"),
    edge("submit_event_log", "success", "cls_search"),
    edge("submit_event_log", "failure", "abap_log"),
    edge("submit_event_log", "failure", "cls_search"),
    # Into the join: four edges, four different nodes.
    edge("request_log", "success", "join_fetch", "in_request_log"),
    edge("req_action_log", "success", "join_fetch", "in_req_action_log"),
    edge("abap_log", "success", "join_fetch", "in_abap_log"),
    edge("cls_search", "success", "join_fetch", "in_cls_search"),
    edge("join_fetch", "out_default", "upload_log"),
    edge("upload_log", "success", "parse_upload"),
    edge("parse_upload", "success", "gate_summary"),
    edge("parse_upload", "failure", "gate_summary"),
    edge("gate_summary", "run", "claude_summary"),
    edge("gate_summary", "skip", "flow_out_plain"),
    edge("claude_summary", "success", "flow_out"),
    # The summary handler returns its errors as DATA and takes the success wire, so this
    # should never fire. If it ever does, answer with the logs and the CR link rather
    # than nothing - flow_out_plain reads neither the summary nor anything downstream.
    edge("claude_summary", "failure", "flow_out_plain"),
    edge("upload_log", "failure", "parse_upload"),
]

# Unwired `failure` ports the audit is told to tolerate — see the note above.
MERGE_FED = ("request_log", "req_action_log", "abap_log", "cls_search", "claude_summary")


# One column per stage, so the canvas reads left to right and no edge doubles back.
# Applied here rather than at each node so the two stay in step when the shape changes.
_LAYOUT = {
    "trigger-1": (60, 60),
    "request_log": (340, -140), "req_action_log": (340, 60),
    "submit_event_log": (340, 260),
    "abap_log": (560, 180), "cls_search": (560, 380),
    "join_fetch": (800, 60), "upload_log": (1040, 60), "parse_upload": (1280, 60),
    "gate_summary": (1520, 60), "claude_summary": (1760, 60),
    "flow_out": (2000, 60), "flow_out_plain": (2000, 300),
}
_RENUMBER = {
    "join_fetch": "6. Wait for every log fetch",
    "upload_log": "7. Upload the collected logs (.md)",
    "parse_upload": "8. Read the file id and build the link",
    "gate_summary": "9. Was the summary asked for?",
    "claude_summary": "10. Summarize the logs (Claude)",
}
for _n in flow_nodes:
    if _n["id"] in _LAYOUT:
        _n["x"], _n["y"] = _LAYOUT[_n["id"]]
    if _n["id"] in _RENUMBER:
        _n["name"] = _RENUMBER[_n["id"]]

flow_body = {
    "name": "[LOG][CS] project + environment + CR -> logs -> CS summary",
    "description": (
        "One log-analysis flow for EVERY project and environment. The cs-worker resolves "
        "the endpoint and the OAuth client from the project + environment inputs (see "
        "cs-worker/app/registry.py), so onboarding a project is four environment variables "
        "and a worker restart - not a new copy of this flow. Cloned from abb79d7a "
        "(MCK QAS) without the deep-analysis branch."),
    "input_schema": [field("reqID", "Change request (reqID)"),
                     field("project", "Project (e.g. MCK, ALCON, S4)"),
                     field("environment", "Environment (e.g. DEV, QAS)"),
                     # Optional, and optional is load-bearing: leaving it out is how a
                     # caller gets the summary, so every existing caller keeps working.
                     field("summarize", "Summarize with Claude? (false to skip)")],
    "output_schema": [], "nodes": flow_nodes, "edges": flow_edges, "metadata": {},
    "schema_version": "1.0.0", "is_confirm": True,
}

print("== [LOG][CS] flow ==")
print(f"  nodes={len(flow_nodes)} edges={len(flow_edges)}")
flow_problems = audit("log flow", flow_nodes, flow_edges, allow_unwired=MERGE_FED,
                      unwired_reason="feeds a MERGE, see MERGE_FED")

with open("cs_log_flow_body.json", "w", encoding="utf-8") as f:
    json.dump(flow_body, f, ensure_ascii=False, indent=1)

if flow_problems:
    raise SystemExit("audit found problems; nothing sent.")

# Update in place when the id file already exists, so re-running does not litter the
# control plane with half-built copies. Re-read the SERVER's copy immediately before the
# PUT: a PUT replaces the whole document, and the local object is not evidence of what is
# published (someone else edits these too).
existing = ""
try:
    with open(state_path("cs_log_flow_id.txt")) as f:
        existing = f.read().strip()
except OSError:
    pass

if not APPLY or DISPATCHER_ONLY:
    FLOW_ID = existing or "<not created yet>"
    print("  [DRY RUN] log flow not sent."
          if not APPLY else "  [--dispatcher-only] log flow left alone.")
elif existing:
    live = get(f"/api/v1/workflows/{existing}")
    body = {**flow_body, "id": existing, "status": live.get("status"),
            "version": live.get("version"), "routing_path": live.get("routing_path", "/")}
    code, data = put(f"/api/v1/workflows/{existing}", body)
    print("  PUT ->", code, "" if code in (200, 201) else json.dumps(data)[:600])
    if code not in (200, 201):
        raise SystemExit(1)
    FLOW_ID = existing
else:
    code, data = post("/api/v1/workflows", flow_body)
    print("  POST ->", code, "" if code in (200, 201) else json.dumps(data)[:600])
    if code not in (200, 201):
        raise SystemExit(1)
    FLOW_ID = data["data"]["id"]
    with open(state_path("cs_log_flow_id.txt"), "w") as f:
        f.write(FLOW_ID)
print("  >>> CS_LOG_FLOW =", FLOW_ID)

# Re-read the SERVER's copy and confirm the graph that landed is the graph we built — an
# edge count read back from the server, not from the object we just sent.
if APPLY and not DISPATCHER_ONLY:
    check = get(f"/api/v1/workflows/{FLOW_ID}")
    print(f"  read back: nodes={len(check['nodes'])} edges={len(check['edges'])} "
          f"status={check.get('status')}")


# ============================================================================
# 2. The CS dispatcher — what the Teams bot actually talks to
# ============================================================================
#
# Deliberately flatter than the per-project dispatchers. Those need a classify node AND a
# per-branch CR pre-check AND a branch per environment, because which project and which
# environment you mean is decided by WHICH BRANCH you land on. Here one node answers all
# of it, so the graph is: gate -> switch -> call -> format.

FMT_CODE = '''NL = chr(10)
# MEASURED, not assumed (probe run through Teams, 25/08): a SINGLE newline renders as
# nothing at all — the two lines simply join. Only a blank line between them, i.e. two
# newlines, is a paragraph break. Every place this code wants a line of its own must use
# PARA; using NL is what made the label and its text run together as
# "Requirement The BA asked…". A `&nbsp;` PARAGRAPH then shows as the empty-looking line
# that gives the sections their gap (as a single-newline neighbour it just became spaces).
PARA = NL + NL
summary = r"""{{$call_logs.output.summary}}"""
reqID = "{{$detect.reqID}}"
project = "{{$detect.project}}"
environment = "{{$detect.environment}}"
logs_url = "{{$call_logs.output.logs_url}}"
cr_url = "{{$detect.cr_url}}"

# The flow appends its own "Attachment:" block; cut it and rebuild it below.
idx = summary.find("Attachment:")
body = summary[:idx] if idx >= 0 else summary

# Each section label on a line OF ITS OWN, with the text beneath it. Keeping the label
# inline ("**Requirement:** The BA asked…") left the sections looking like one block in
# Teams, because a single blank line renders tight there. A label alone on its line is
# the separation that survives whatever the renderer does with blank lines.
for label in ("Requirement:", "Analysis summary:", "Suggested action:"):
    body = body.replace(label, PARA + "&nbsp;" + PARA + "**" + label[:-1] + "**" + PARA)


# The numbered actions arrive inline, all inside the Suggested action paragraph
# ("... escalation. 1) In the RECORDSUPPLEMENT section ... 2) Field validFrom ...").
# Give each its own line. Plain replaces rather than a regex: nothing to escape through
# two layers of quoting, and the match is narrow enough (space, digit, ')', space).
for _n in range(1, 10):
    body = body.replace(" " + str(_n) + ") ", PARA + str(_n) + ") ")

# THE ONE THAT BROKE THE RENDERING: the model separates its sections with runs of
# spaces, and after the replaces above those are stranded on lines of their own. A line
# holding four spaces is not blank to Markdown — it opens an indented CODE BLOCK, which
# swallowed every gap between sections in Teams. Strip trailing whitespace per line so
# such a line becomes genuinely empty, then collapse runs of blank lines to one.
# strip(), not rstrip(): a line that BEGINS with four spaces opens a code block just as
# surely as one made only of spaces, and the model's own indentation is the likeliest
# source of that. Nothing in this reply is meant to be indented, so leading space is
# always noise here.
body = NL.join(line.strip() for line in body.split(NL))
while NL * 4 in body:
    body = body.replace(NL * 4, NL * 3)
body = body.strip()

# When the user asked for the logs alone, the flow answers with its own one-line
# placeholder. Printing that sentence at a person would be showing them the plumbing.
skipped = body.startswith("Summary skipped")


# The change request in the heading IS the link — that is where a reader looks first,
# and it saves a line at the bottom. A landscape with no web app resolves cr_url to ""
# and simply keeps the plain number; never link somewhere that does not exist.
heading_id = ("[" + reqID + "](" + cr_url + ")") if cr_url else reqID

# The reply must START with the heading. The relay prepends "🤖 " to the first line of
# whatever it is given, so anything put in front here lands AFTER that marker and leaves
# it stranded on a line of its own ("🤖" alone, heading below). Tried 25/08, reverted.
# A gap between the echoed question and the bot's answer therefore cannot come from here
# at all - it has to come from the relay, which composes both lines.
if skipped:
    # Different verb and a different mark, so a reader can tell at a glance that this
    # answer is short because they asked for it to be - not because something failed.
    reply = ("\\U0001F4C4 **Logs - " + heading_id + "** (" + project + " " + environment + ")"
             + PARA + "No analysis was requested. The collected logs are attached."
             + PARA + "**Attachment:**" + NL
             + "- All related logs: [Download](" + logs_url + ")")
else:
    reply = ("\\u2705 **Log analysis - " + heading_id + "** (" + project + " " + environment + ")"
             + PARA + body + PARA
             + "**Attachment:**" + NL
             + "- All related logs: [Download](" + logs_url + ")")

return {"reply": reply}
'''

detect = node(
    "detect", "1. Read the request (project + environment + CR)", "TASK",
    "worker:cs_detect_request",
    {"message": "{{$input.message}}",
     "check_cr_exists": True,
     # `default_project` is deliberately NOT bound to {{$input.project}}.
     #
     # The router sends the BOT SLUG as `project`, and binding it here looked free: a bot
     # dedicated to one project would resolve "CR123 in DEV" without the user naming it.
     # It cost a hard failure instead. Measured 2026-08-24: a caller that posts only
     # {"message": "..."} — which is every direct API caller, since only the router sends
     # a slug — dies at 9 s with "input resolution failed: Key 'project' not found". A
     # missing key is fatal to expression resolution, not blank.
     #
     # It also bought nothing today: the only slug that reaches here is "cs", which is not
     # a configured project, so the registry ignores it. A bot dedicated to one project can
     # still get the behaviour by typing that project into the node field as a literal.
     "timeout_secs": 90},
    300, 300, TASK_PORTS)

route = node(
    "route", "2. Run, or answer without running", "SWITCH", "builtin:switch",
    {"data": [{"key": "{{$detect.decision}}", "operations": "==", "value": v, "branch": v}
              for v in ("run", "stop")]},
    560, 300,
    {"in": PORT_IN, "out": [port("run", "run"), port("stop", "stop"),
                            port("default", "default")]})

# On `run`, which of the two things did they ask for? A SECOND switch, not another rule
# on the first: a cancel whose CR does not exist has decision=stop, and must still reach
# say_stop with the gate's sentence rather than the cancel flow.
route_intent = node(
    "route_intent", "3. Analyse, or cancel?", "SWITCH", "builtin:switch",
    {"data": [
        {"key": "{{$detect.intent}}", "operations": "==", "value": "cancel_request",
         "branch": "cancel", "port_id": "cancel"},
        # The catch-all the engine does not have: $workflow.id is seeded at start_run, so
        # this can neither fail to resolve nor fail to match. Anything that is not an
        # explicit cancellation reads the logs, which is the harmless direction.
        {"key": "{{$workflow.id}}", "operations": "!=", "value": "__never__",
         "branch": "logs", "port_id": "logs"}]},
    690, 300,
    {"in": PORT_IN, "out": [port("cancel", "cancel"), port("logs", "logs")]})

call_cancel = node(
    "call_cancel", "4. Cancel the change request", "WORKFLOW", "builtin:workflow",
    {"workflow_id": "26df8687-df15-499b-a1b0-95e05cb1e8b5", "wait_for_completion": True,
     "input_mapping": {"reqID": "{{$detect.reqID}}",
                       "project": "{{$detect.project}}",
                       "environment": "{{$detect.environment}}",
                       # A LITERAL, not {{$input.user_name}}. A missing key is fatal to
                       # expression resolution, not blank -- the same trap documented on
                       # `detect` above, and a direct API caller posts only a message.
                       "comment": "Cancelled via the SMDG CS bot"}},
    820, 420, {"in": PORT_IN, "out": [port("success", "Success"), port("failure", "Failure")]})

call_logs = node(
    "call_logs", "3. Analyse the logs", "WORKFLOW", "builtin:workflow",
    {"workflow_id": "", "wait_for_completion": True,
     "input_mapping": {"reqID": "{{$detect.reqID}}",
                       "project": "{{$detect.project}}",
                       "environment": "{{$detect.environment}}",
                       # Safe to bind, unlike {{$input.summarize}}: `detect` always
                       # emits this field, so there is no missing key to resolve.
                       "summarize": "{{$detect.summarize}}"}},
    820, 200, {"in": PORT_IN, "out": [port("success", "Success"), port("failure", "Failure")]})
call_logs["parameters"]["workflow_id"] = FLOW_ID

fmt = node("fmt", "4. Format the reply for Teams", "TASK", "worker:code",
           {"language": "python", "mode": "all_items", "timeout_seconds": 30,
            "code": FMT_CODE},
           1080, 200, TASK_PORTS)


def set_var(nid, expr, x, y):
    return node(nid, nid, "SET_VARIABLE", "builtin:set_variable",
                {"variables": [{"name": "reply", "type": "string", "item_type": "",
                                "value_expression": expr}]},
                x, y, {"in": PORT_IN, "out": PORT_OUT})


say_done = set_var("say_done", "{{$fmt.result.reply}}", 1340, 200)
# The cancel flow already writes the sentence, including the refusal cases, so this only
# carries it through.
say_cancel = set_var("say_cancel", "{{$call_cancel.output.reply}}", 1340, 440)
# The gate already wrote this sentence, in the user's own language where it could tell.
say_stop = set_var("say_stop", "{{$detect.message_to_user}}", 1340, 380)
say_failed = set_var(
    "say_failed",
    "❌ I could not analyse the logs for that request. Please try again, or ask the "
    "SMDG-AI team to check the workflow run history.", 1340, 560)
say_detect_failed = set_var(
    "say_detect_failed",
    "❌ I could not read that message. Please say it again, and include the change "
    "request number, the project and the environment.", 1340, 720)

bot_out = node(
    "out", "Output", "OUTPUT", "builtin:output",
    {"output_schema": [
        {"key": "reply", "type": "string", "value": "{{$variables.reply}}",
         "field_id": "dddddd01-1111-4111-8111-111111111111"},
        {"key": "decision", "type": "string", "value": "{{$detect.decision}}",
         "field_id": "dddddd02-2222-4222-8222-222222222222"},
        {"key": "intent", "type": "string", "value": "{{$detect.intent}}",
         "field_id": "dddddd03-3333-4333-8333-333333333333"},
        {"key": "target", "type": "string", "value": "{{$detect.target}}",
         "field_id": "dddddd04-4444-4444-8444-444444444444"},
    ]},
    1600, 380, {"in": PORT_IN, "out": PORT_OUT})

bot_trigger = node("start", "Start", "TRIGGER", "builtin:trigger",
                   {"trigger_type": "manual"}, 60, 300, {"in": [], "out": PORT_OUT})

bot_nodes = [bot_trigger, detect, route, route_intent, call_logs, call_cancel, fmt,
             say_done, say_cancel, say_stop, say_failed, say_detect_failed, bot_out]

# The `failure` port of a WORKFLOW node is INERT -- F13 measured it with three throwaway
# workflows, and run 494c7a33 showed the same signature on the real fleet: after
# NodeFailed(call_child) there is no EdgeTraversed, no WireSkipped, no WireDeadLetter. The
# parent never considers the wire.
#
# So these ports are left unwired ON PURPOSE, and the audit is told which ones. Wiring them
# to say_failed (as this file did) reads like failure is handled here; it is not, and a
# reader who believes it will not build the guard that would actually help. The real guard
# is inside the child -- the cancel flow's own say_crashed -- because that is the only
# place a child's crash can still produce a sentence.
WORKFLOW_FAILURE_INERT = ("call_logs", "call_cancel")

bot_edges = [
    edge("start", "out_default", "detect"),
    edge("detect", "success", "route"),
    edge("detect", "failure", "say_detect_failed"),
    edge("route", "run", "route_intent"),
    edge("route_intent", "logs", "call_logs"),
    edge("route_intent", "cancel", "call_cancel"),
    edge("call_cancel", "success", "say_cancel"),
    edge("route", "stop", "say_stop"),
    # A SWITCH with no matching branch and no default edge STALLS the run. The default
    # goes to say_stop, which carries whatever sentence the gate wrote — never silence.
    edge("route", "default", "say_stop"),
    edge("call_logs", "success", "fmt"),
    edge("fmt", "success", "say_done"),
    edge("fmt", "failure", "say_failed"),
    edge("say_done", "out_default", "out"),
    edge("say_cancel", "out_default", "out"),
    edge("say_stop", "out_default", "out"),
    edge("say_failed", "out_default", "out"),
    edge("say_detect_failed", "out_default", "out"),
]

BOT_INPUT = [field("message", "User message"),
             field("text", "User message (alternate key)"),
             field("project", "Bot slug sent by the router (NOT the SMDG project)"),
             field("bot_id", "Bot id, verified from the JWT aud claim"),
             field("thread_message_id", "Teams thread message id"),
             field("user_name", "User name"),
             field("user_aad_id", "User AAD id"),
             field("channel_id", "Teams channel id")]

bot_body = {
    "name": "[BOT] CS dispatcher (any project + environment)",
    "description": (
        "Customer-service dispatcher. One node (cs_detect_request) reads the message, "
        "decides the project, the environment and the change request, checks all three "
        "exist, and returns run or stop; the run branch calls the single dynamic log "
        "flow. Unlike the per-project dispatchers it needs no branch per project or "
        "environment - adding a project is four environment variables on the cs-worker, "
        "and this workflow does not change."),
    "input_schema": BOT_INPUT,
    "output_schema": [], "nodes": bot_nodes, "edges": bot_edges, "metadata": {},
    "schema_version": "1.0.0", "is_confirm": True,
}

print("\n== [BOT] CS dispatcher ==")
print(f"  nodes={len(bot_nodes)} edges={len(bot_edges)}  calls {FLOW_ID}")
bot_problems = audit("dispatcher", bot_nodes, bot_edges,
                     allow_unwired=WORKFLOW_FAILURE_INERT,
                     unwired_reason="inert on a WORKFLOW node, F13")
with open("cs_dispatcher_body.json", "w", encoding="utf-8") as f:
    json.dump(bot_body, f, ensure_ascii=False, indent=1)
if bot_problems:
    raise SystemExit("audit found problems; nothing sent.")
if "<not created yet>" in json.dumps(bot_body):
    # The child id never resolved, so call_logs/call_cancel would point at a placeholder.
    # The audit cannot see this -- the graph is well formed, it just calls nothing.
    raise SystemExit(
        "dispatcher body still contains '<not created yet>' — the child workflow id did "
        "not resolve, so this would ship a dispatcher that calls nothing. Nothing sent.")
if not APPLY:
    print("\n[DRY RUN] dispatcher not sent.")
    raise SystemExit(0)

existing_bot = ""
try:
    with open(state_path("cs_dispatcher_id.txt")) as f:
        existing_bot = f.read().strip()
except OSError:
    pass

if existing_bot:
    live = get(f"/api/v1/workflows/{existing_bot}")
    body = {**bot_body, "id": existing_bot, "status": live.get("status"),
            "version": live.get("version"), "routing_path": live.get("routing_path", "/")}
    code, data = put(f"/api/v1/workflows/{existing_bot}", body)
    print("  PUT ->", code, "" if code in (200, 201) else json.dumps(data)[:600])
    if code not in (200, 201):
        raise SystemExit(1)
    BOT_ID = existing_bot
else:
    code, data = post("/api/v1/workflows", bot_body)
    print("  POST ->", code, "" if code in (200, 201) else json.dumps(data)[:600])
    if code not in (200, 201):
        raise SystemExit(1)
    BOT_ID = data["data"]["id"]
    with open(state_path("cs_dispatcher_id.txt"), "w") as f:
        f.write(BOT_ID)
print("  >>> CS_DISPATCHER =", BOT_ID)
check = get(f"/api/v1/workflows/{BOT_ID}")
print(f"  read back: nodes={len(check['nodes'])} edges={len(check['edges'])} "
      f"status={check.get('status')}")
