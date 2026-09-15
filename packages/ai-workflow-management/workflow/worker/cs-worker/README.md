# SMDG CS Worker

A customer-service worker: it reads a chat message, works out **which project, which
environment and which change request** the user means, checks that all three exist, and
then fetches and explains that environment's SMDG process logs.

It is the `claude-worker` and the `log-worker` fused, with one thing added that neither
has: **the set of projects and environments is data, not code.**

```
LOG_WORKER_CLIENT_ID_MCK_DEV=…      ┐
LOG_WORKER_CLIENT_SECRET_MCK_DEV=…  │  four variables + `docker compose up -d`
LOG_WORKER_APP_PREFIX_MCK_DEV=…     │  = MCK / DEV is live
LOG_WORKER_REGION_MCK_DEV=us20      ┘
```

No code change, no new workflow. That is the whole point of this worker.

## Why it exists

The log-worker names its customers in a Python literal, and each workflow node carries a
hard-coded `api_url` and `token_url`. So a project+environment costs a code change **and**
its own copy of the log-analysis flow — which is why tenant-1 has
`[LOG][MCK_DEV]`, `[LOG][MCK_QAS]`, `[LOG][ALCON][DEV]`, `[LOG][s4-qas]`, and a dispatcher
per project on top of them. Every one of those is a place to fix a bug twice.

Here the node carries `project` + `environment`, and the worker resolves both the endpoint
and the OAuth client from its own registry. **One** log flow and **one** dispatcher serve
everything.

Proven on 2026-08-24, same workflow, two different customers on two different CF regions:

| run | message | result |
|---|---|---|
| `c5fe3b15` | `CR0000000134 in ALCON project with DEV environment` | real analysis, br10 |
| `797e0be6` | `CR0000004540 in MCK project with DEV environment` | real analysis, us20 |

Current timings, measured after the move to the SDK's multi-type runner: log flow
**45–54 s**; through the dispatcher **61–76 s** when it analyses, **20–25 s** when it
declines.

## The node types

All eight are prefixed `cs_`, deliberately. This worker leases from the same executor, in
the same blank tenant, as the log-worker and claude-worker already on this host — two
workers registering the same `worker_type` would split that queue and a task would land on
whichever leased it first, intermittently.

| worker_type | what it does |
|---|---|
| `cs_detect_request` | **the gate.** Message → project + environment + CR, validated, plus a CR-existence probe. Returns `decision = run \| stop` and the sentence to say when it stops. |
| `cs_request_log` | "what the user sees" — the request-level error string |
| `cs_request_action_log` | the action trail |
| `cs_submit_event_log` | the validation steps; carries the `mdgLogID` |
| `cs_abap_log` | the ABAP-side MD API log, where the landscape has one |
| `cs_cloud_logging_search` | BTP Cloud Logging (one instance for the whole landscape) |
| `cs_summarize_log` | the BA-readable analysis (same prompt as the claude-worker's) |
| `cs_targets` | what this worker is configured for — the smoke test after adding a project |

## Adding a project

1. Put four lines in `.env` (see `.env.example`):

   ```
   LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV>=…
   LOG_WORKER_CLIENT_SECRET_<PROJECT>_<ENV>=…
   LOG_WORKER_APP_PREFIX_<PROJECT>_<ENV>=<the CF app-name prefix>
   LOG_WORKER_REGION_<PROJECT>_<ENV>=<br10 | us20 | …>
   ```

2. `docker compose up -d`
3. Read the boot line: `[cs-worker] targets: ALCON (DEV, QAS); MCK (DEV, QAS); S4 (QAS)`

The last underscore separates PROJECT from ENVIRONMENT, so `S4_QAS` is project `S4`,
environment `QAS`. From the prefix and region the worker derives:

```
token : https://<prefix>.authentication.<region>.hana.ondemand.com/oauth/token
api   : https://<prefix>-srv-process-system.cfapps.<region>.hana.ondemand.com/CommonProcessService
abap  : https://<prefix>-srv-config-admin.cfapps.<region>.hana.ondemand.com/AdminService/<entity>
```

A landscape that does not fit can override any of them per target
(`LOG_WORKER_API_URL_<T>`, `…_TOKEN_URL_<T>`, `…_ABAP_URL_<T>`).

**A half-configured target is not silently dropped.** It stays in the registry with a
`problem` string naming the missing variable, and `cs_targets` reports it — so "why is MCK
PRD not showing up?" has an answer instead of a shrug.

## Things measured here that are worth knowing

**Quote every value in `.env`.** `docker compose` interpolates `$…` inside an `env_file`.
An unquoted secret containing `$` arrives 15 characters shorter, silently, and OAuth
answers `invalid_client / Bad credentials` — which reads like a rotated secret, not a
truncated one. Measured on this very file: `MCK_DEV` 81 chars → 66, `MCK_QAS` 81 → 37.
(`docker run --env-file`, by contrast, is not quote-aware and passes the quotes through —
so for a one-off use `docker compose run --rm --no-deps cs-worker …`, not `docker run`.)

**The ABAP log does not exist everywhere.** Asked each landscape's `AdminService` for its
own entity list, 2026-08-24:

| target | serves |
|---|---|
| MCK DEV | `MDApiLog`, real rows |
| S4 QAS | `MDApiLog`, real rows — `CeAppLogMsg`, which the published S4 flow uses, answers **502** |
| MCK QAS | neither — the service exposes CompanySetting/GlobalSettings/… |
| ALCON DEV, ALCON QAS | neither — a different AdminService entirely (Questionnaires/Questions/…) |

The published per-project flows point at `MDApiLog` on all of them, so that node has been
returning a 404 on three of five landscapes — invisibly, because a failed fetch still
reports the task as SUCCESS. Here `LOG_WORKER_ABAP_ENTITY_<T>=none` marks a landscape that
has no ABAP log and the node says so in one sentence, in 0.0 s, instead of spending a round
trip on a 404 nobody reads.

**A failed fetch still reports the task as SUCCESS.** Handlers never raise; a 401, a 404 or
a network blip returns `{"status": "FAILED", "error": …}` as *data* and the node goes green.
Every downstream node must gate on `status` / `error` — nothing in the platform does it for
you. That contract is why `cs_detect_request` probes the CR *before* the flow spends an LLM
call producing a confident analysis of nothing.

**The gate fails open.** It stops only on a definite verdict — a malformed number, an
unknown project or environment, or an explicit 404. A DNS blip or an expired token lets the
request through, because a pre-check that fails closed is a wall, and it gets built on a day
when the endpoint is fine and torn down on the day it is not.

## Running it

```bash
cd .../workflow/worker/cs-worker
docker compose up -d --build     # build + run
docker compose logs -f           # the boot line lists the targets
docker compose up -d             # after editing .env — this is how a project goes live
```

A one-off check, without disturbing the running container:

```bash
docker compose run --rm --no-deps -T cs-worker python -c \
  "from app.node_types import NODE_TYPES; \
   print([n.worker_type for n in NODE_TYPES]); \
   print(next(n for n in NODE_TYPES if n.worker_type=='cs_targets').handler({},{})['catalogue'])"
```

## The workflows it drives

Built by `workflow/build_cs_flows.py` (dry run by default). Both are **DRAFT** on tenant-1:

| workflow | id |
|---|---|
| `[LOG][CS] project + environment + CR -> logs -> BA summary` | `3ad8ac75-f712-409c-b8dd-73b05ee5529f` |
| `[BOT] CS dispatcher (any project + environment)` | `98a64446-875f-4002-9f7a-28dfe06dbdb8` |

`workflow/router_add_cs.py` adds a `cs` branch to the shared bot router
(`105b6ad5-d29a-43dc-8ddc-ada23983eff9`). **Applied and published 2026-08-24.** The change
is additive; the read-back of the *published* copy shows all four branches and the
`default` edge still carrying its `condition`:

```
smdg -> call_smdg   mck -> call_mck   alcon -> call_alcon   cs -> call_cs
default -> say_unknown_bot  condition='default'
```

Proven through the published router, not just the dispatcher:

| project | result |
|---|---|
| `cs` | `route_bot -> call_cs -> detect -> … -> say_cs`, full ALCON DEV analysis |
| `mck` / `alcon` / `smdg` | unchanged — each still reaches its own dispatcher and answers |

The publish gate is the ROUTER's own in-flight runs, not the tenant's. This control plane
always carries a handful of `CREATED` runs belonging to unrelated workflows that never
start; gating on those would mean never publishing anything.

## The Teams bot

`CS Bot` — created 2026-08-24 with `~/.teams-bots/create_cs_bot.cmd`.

| | |
|---|---|
| Teams app id / bot id | `9d260633-8a73-4971-8c90-f9628b875af3` |
| endpoint | the SHARED relay, `https://smdg-teams-relay.cfapps.br10.hana.ondemand.com/api/messages` |
| credentials | `~/.teams-bots/.env.cs` — **the only copy**; losing it means creating the bot again |
| install link | `https://teams.microsoft.com/l/app/9d260633-8a73-4971-8c90-f9628b875af3?installAppPackage=true&appTenantId=ddc6e257-f88c-4362-b225-e0707e51d095` |

Several bots share one relay endpoint on purpose — the relay tells them apart by the JWT
`aud`, so a new bot needs three environment variables and no relay code change. The
variable names decide the slug: `BOT_CS_ID` / `_SECRET` / `_TENANT` → slug `cs` → the
router's `cs` branch.

`~/.teams-bots/finish_cs_relay.ps1` does that half. It needs a live `cf` session and
**verifies with a real API call, not `cf target`** — that prints org/space/user perfectly
from cache long after the token has died, which is how an expired token reads as a healthy
one.

Ran 2026-08-24: three `cf set-env`, then `/healthz` went from
`bots: [alcon, mck, smdg]` to `bots: [alcon, cs, mck, smdg]` with `bots_missing_tenant`
empty.

**`cf restart --strategy rolling` worked this time**, which contradicts the
onboard-project-bot hackathon's finding F3 (measured 20/08: `memory quota_exceeded for
organization smdg-ai-tenant-1`). So the other three bots were **not** interrupted. Do not
read either measurement as permanent — the script still tries rolling first and falls back
to a plain restart, and a plain restart does take the other projects' bots down for
30–60 s. Whoever onboards the next project should expect either outcome and schedule for
the worse one.

The bot only becomes reachable in Teams once someone installs the app package from the
link above.

### Why the log flow is a chain and not a fan-out

The flow it was cloned from fetches its five logs in parallel and joins them in a MERGE.
Copying that shape produced two distinct failures on 2026-08-24:

* **run `7e6bdfb6`** — a merge that never closed. A MERGE with `strategy=all` waits for one
  arrival per **incoming edge**, not per in-port; wiring each node's `failure` port into the
  same merge port made it expect 8 arrivals for 4 branches (the other four log as
  `WireSkipped`). All five log nodes completed and the merge sat there until its timeout.
* **runs `258a9195` and `2674c005`** — `Orchestration failed while processing continue:
  expected version 7/9`, an optimistic-concurrency conflict on the run's event stream. Both
  were **child** runs, called from the dispatcher. Five direct runs of the same flow in the
  same hour all completed; the conflict appeared only while a parent was waiting on the
  child.

Serialising the fetches closed both. Five serial dispatcher runs afterwards: 5/5 completed.

**The chain then looked expensive, and it was — but not for the reason it appeared.**
Timing a run node by node (`workflow/measure_flow.py`) showed each fetch taking 17–22 s
wall clock for ~2.5 s of actual OData work. That 15–20 s was queue latency from the FORKED
lease loop this worker carried: one round robin across all eight node types, so a chained
flow waited for its slot per node. Fan-out looked worth 61 s on a 117 s run.

Moving to the SDK's multi-type runner — a lease loop per node type — removed it:

| | forked loop | SDK runner |
|---|---|---|
| log flow, end to end | 117 s | **45–54 s** |
| the five fetches | 84 s | **20–25 s** |
| per fetch node | 17–22 s | 1.2–10 s |
| what fan-out would save | 61 s | **12–15 s** |

So the shape was never the problem. But the fan-out question deserved a real answer rather
than caution, so it got one: `workflow/build_fanout_variant.py` builds the parallel shape
as a separate DRAFT (`692ca1ae`), with a cloned dispatcher pointing at it, and
`workflow/ab_test.py` alternates the two arms so load falls on both equally.

**Re-measured 2026-08-25, and the answer got sharper, not softer:**

| | fan-out | chain |
|---|---|---|
| runs completed | **0 / 3** | **6 / 6** |
| failure | `expected version 11, found 1`, `expected version 8, found 9` | — |
| worst case | the PARENT hung in `RUNNING` forever — its child died without reporting back | — |

The mechanism, from run `becbcbb0`'s event log: three branches finished within **1.3
seconds** of each other, each writing a `NodeInput` for the merge, and the optimistic
concurrency check on the run's event stream lost.

```
6.07s  NodeCompleted  request_log        → NodeInput join_logs
6.76s  NodeCompleted  submit_event_log   → NodeInput abap_log, cls_search
7.32s  NodeCompleted  req_action_log     → NodeInput join_logs
       ✗ expected version 11, found 1
```

And here is the part worth remembering: **the old forked lease loop was hiding this bug.**
Its 17–22 s of queue latency per node spaced the branch completions far enough apart that
they rarely collided — which is why fan-out mostly worked before, and why the earlier
evidence looked thin and inconsistent. Making the worker fast made the parallel shape
*less* viable, not more. Two changes that look unrelated were coupled through timing.

The chain stays, on evidence. The conflict is a platform-level race for the workflow-engine
team, and it is now cheap to reproduce: run `692ca1ae` once. Run ids `becbcbb0`,
`1e1a6d1a`, and the hung parent `77a3ef20`.

## What is NOT in this image

Compared with the claude-worker's: no `git` (nothing is cloned) and no Playwright/Chromium
(nothing is reproduced in a browser). That is ~400 MB and a browser install on every
rebuild, for capabilities a log lookup does not use. Node.js is present for exactly one
reason — the Claude Code CLI that `cs_detect_request` and `cs_summarize_log` shell out to.

## Files

```
cs-worker/
├── app/
│   ├── registry.py        the project × environment registry, built from os.environ
│   ├── detect_request.py  the gate (Claude + registry + CR probe)
│   ├── fetch_log.py       the four OData fetchers, addressed by project + environment
│   ├── claude_cli.py      one Claude call with token rotation, lifted from the engine
│   ├── secrets.py         CLS + Claude credentials (project ones live in registry.py)
│   ├── summarize_log.py   the BA analysis node
│   ├── node_types.py      the eight cs_* registrations
│   ├── fetch_cls.py       ┐ copied verbatim from log-worker
│   └── worker_trace.py    ┘ (production-proven; nothing worker-specific in them)
├── workflow/              the scripts that build and smoke-test the two workflows
├── Dockerfile
├── docker-compose.yml
└── .env.example
```
