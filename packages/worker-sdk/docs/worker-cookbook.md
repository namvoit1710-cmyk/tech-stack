# Worker Cookbook — push, pull, many nodes, and reading data

**Audience:** you are building a worker in a hackathon and want it working today, and still
working in three months.

**Relationship to [`../README.md`](../README.md):** the README is the reference — install,
scaffold, the seven-step tutorial, interfaces, Docker, the API table. It documents SERVER and
HEADLESS. This cookbook is the part that is not in it: **PULL mode, many node types in one
worker, and how input data actually arrives** — plus the traps that only show up in
production. Read the README first if you have never built a worker; come here when you are
choosing a mode or your inputs are not what you expected.

Every claim below is anchored to a `file:line` in this SDK at the time of writing. When in
doubt, the code wins — go read it.

---

## 0. Read this table before you choose anything

Not every mode can do everything. This is the single most expensive thing to learn late,
because the gaps are silent: the worker starts, registers, looks healthy, and then behaves
differently from the one next to it.

| | **SERVER** (push) | **PULL** | **HEADLESS** | **gRPC** |
|---|---|---|---|---|
| How work arrives | executor `POST`s to you | you long-poll a lease endpoint | **nothing arrives** | executor gRPC call |
| Needs a reachable URL | **yes** (`PROXY_URL`) | no | no | yes (port) |
| Many node types in one process | **yes** | **yes** | registers only, cannot dispatch | **no** |
| Worker functions (`/functions`) | **yes** | **no** | **no** | yes (list + invoke) |
| Resolves `__file_ref` inputs for you | **yes** | **no — you do it** | n/a | **no — you do it** |
| Streams large outputs to files | **yes** | **no** | n/a | **no** |
| Concurrency gate / 429 backpressure | **yes** | no (batch size is the limit) | n/a | no |

Where those come from: mode dispatch `worker_sdk/runner.py:79-100`; multi-type dispatch is
`restful/v1/routes.py:272` (SERVER) and `worker_pull::_run_multi_type_pull` (PULL), with
HEADLESS registering types it cannot dispatch to (`worker_headless/__init__.py:93`);
`function_registry` is read only in `routes.py:468` and the gRPC servicer; `file_ref_resolver`
and the output converter appear **only** in `routes.py` (zero references in `worker_pull/`,
`worker_headless/`, `grpc/`).

**Practical reading:**

* Want several nodes from one deployable? **SERVER or PULL** — both dispatch to a per-type
  handler. SERVER if the executor can reach you; see §3.4 for how PULL differs.
* Cannot be reached from the executor (laptop, locked-down network, no route)? **PULL** — and
  accept no worker functions, and resolve your own file refs.
* HEADLESS is not a way to receive tasks. Its main loop
  (`worker_headless/__init__.py:141-172`) registers and heartbeats, nothing more. Use it when
  *you* supply the intake (embedding the SDK, your own consumer).

> `WORKER_DELIVERY_MODE` (`app_config.py:37`) is **declared and never read** — grep it, there
> is exactly one hit, its own definition. Setting it to `"pull"` does nothing. The switch is
> `APP_MODE=PULL`.

---

## 1. The one invariant: the exec core never touches the event loop

Before any mode-specific detail, the rule that every transport obeys and every embedder
forgets.

`ExecuteTaskUseCase.execute` is **sync on purpose** — a handler may burn CPU or block for
minutes. Every transport therefore hands it to a thread, and the SDK refuses to run it
otherwise (`execute_task_usecase.py:32-62`):

```python
def assert_off_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return                      # correct: no loop on this thread
    raise RuntimeError("worker-sdk: ExecuteTaskUseCase.execute() was called ON an "
                       "asyncio event loop. … Dispatch it with asyncio.to_thread(...)")
```

Why it is a hard error rather than a warning — the failure it prevents is remote and delayed:

* **PULL** — lease renewal and the heartbeat are asyncio tasks on that same loop. Neither gets
  scheduled. The lease expires, the executor re-enqueues the task **to another worker**, and
  your callback is rejected later on a dead `lease_token`. You see it as a duplicate execution
  minutes later, not as a stall.
* **SERVER** — every other in-flight request on the pod queues behind it.

So: `await asyncio.to_thread(exec_uc.execute, cmd)`. Never `exec_uc.execute(cmd)` from async
code. The check is repeated at the pull dispatch boundary
(`worker_pull/__init__.py:211-220`) so an injected/duck-typed exec core is held to it too.

**Your handler may be sync or async — both are fine.** `_invoke_node_type_handler`
(`routes.py:197-208`) awaits an `async def` directly and pushes a plain `def` through
`asyncio.to_thread`. Pick sync for CPU/blocking libraries, async for `httpx.AsyncClient`.
The pool for sync handlers is bounded by `MAX_WORKER_THREADS` (`app_config.py:136`, default 8,
sized for a 256 MB pod at ~10 MB per concurrent sync handler). Async handlers never touch that
pool and have no per-pod ceiling.

---

## 2. Push mode (SERVER) end to end

The default. The executor holds your URL and calls you.

### 2.1 Minimum viable worker

```python
# main.py
import os
from worker_sdk import run_worker
from app.layer4_frameworks.config import settings as app_settings

if __name__ == "__main__":
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(os.path.dirname(__file__),
                                   "app", "layer2_application", "features"),
        base_module="app.layer2_application.features",
    )
```

`run_worker` merges your `Settings` subclass into the SDK singleton **before any service is
built** (`runner.py:66-67`), so precedence is **env/.env > your subclass default > SDK
default**. Only fields the SDK singleton declares are merged (`runner.py:38-39`) — your own
extra fields stay on your `app_settings` and you read them directly from there.

Feature discovery is convention-based (`bootstrap.py:38-48`): a package `execute_task` under
your features path must export `ExecuteTaskUseCase`; it lands in the container as
`execute_task_usecase`. A typo in the class name is a silent no-op — the worker starts and
answers "No executor available".

Dependencies are injected **only if you ask for them by name** (`bootstrap.py:120-127`):

```python
class ExecuteTaskUseCase:
    def __init__(self, logger, monitor, file_ref_resolver=None, **kwargs):
        ...
```

Available names: `logger`, `monitor`, `app_config`, `storage`, `input_reader`,
`output_writer`, `worker_registry`, `function_registry`, and `file_ref_resolver`
(**only when `FILE_SERVICE_URL` is set and `RESOLVE_FILE_REFS` is true** — `bootstrap.py:98`).
Always keep `**kwargs`.

### 2.2 The two endpoints, and which one you actually get

| | `POST /api/v1/execute` | `POST /api/v1/execute-async` |
|---|---|---|
| Returns | the result | **202 `{accepted: true}`**, result via callback |
| Defined | `routes.py:267` | `routes.py:323` |
| Backpressure | none | **429 `WORKER_SATURATED`** when full |

**Both must work.** They are separate code paths with separate bugs; a change tested on
`/execute` alone routinely ships broken on `/execute-async`, because the async path runs
inside a FastAPI `BackgroundTask` where an exception has nowhere to go. That is why input
resolution on the async path is individually wrapped and turned into an **error callback**
rather than allowed to raise (`routes.py:381-394`) — an unhandled background exception posts
nothing, and the node hangs until its deadline.

**Callbacks retry.** `_post_callback` (`routes.py:106-148`) makes 3 attempts with linear
backoff, treats 4xx as final (the task is not yours to complete) and 5xx/transport as
transient. This exists because it was once a bare `client.post(...)` with no
`raise_for_status()` — httpx does not raise on 5xx, so a rejected result was discarded with no
log and the node hung forever.

**Saturation is 429, not 503** (`routes.py:332-350`). Set `WORKER_MAX_CONCURRENT`
(`app_config.py:146`, `0` = unlimited) and the async path claims a slot *before* answering
202; at capacity it returns 429 + `Retry-After` so the executor **requeues** instead of
failing the task. `/ready` deliberately stays `true` when saturated — busy is not unhealthy,
and the platform must not restart a full pod. Sizing: async/IO-bound 32–64, sync handlers
`<= MAX_WORKER_THREADS`, CPU-bound ≈ cores.

### 2.3 Registration and self-healing

The lifespan (`worker_server/__init__.py:61-224`) registers, heartbeats every
`HEARTBEAT_INTERVAL_SECONDS`, and deregisters. Two behaviours worth knowing because they look
like bugs when you watch the logs:

* A failed boot registration parks a **blank** worker id and the heartbeat loop retries it
  (`worker_server/__init__.py:108-120`). A worker that boots against a dead executor recovers
  within one interval instead of never.
* The executor can answer a heartbeat with `re_register: true`; the SDK re-registers and logs
  it. Seeing that after an executor restart is normal.

Registration is built **before** any network call, so a failure there is a config error (blank
`WORKER_TYPE`) and is reported differently from a dead executor. Blank `WORKER_TYPE` is fatal
by default (`_assert_worker_type`, `worker_server/__init__.py:22-34`); the
`SDK_ALLOW_EMPTY_WORKER_TYPE=true` escape hatch exists for emergencies, not for you.

---

## 3. Pull mode end to end

Use when the executor cannot reach you. The worker registers with `endpoint="pull"` and no
server, then long-polls for leases.

### 3.1 Turning it on

```bash
APP_MODE=PULL
WORKER_TYPE=my_worker            # what you lease
REGISTRY_URL=https://<executor>  # lease + callback live here
WORKER_TENANT=<tenant>           # sent as X-Tenant-Id; scopes the lease partition
LEASE_RENEW_ENABLED=true         # ON if any task can outlive the lease TTL
```

`main.py` is unchanged — the mode is pure config (`runner.py:94-97`).

> **If you are on an SDK older than this document, PULL will not start.** It resolved the exec
> core from `container["execute_task"]`, but `build_app_container` names every discovered
> feature `<module>_usecase` (`bootstrap.py:45`) — so a container built the documented way
> carries `execute_task_usecase` and never the short name, and every pull worker died at boot
> with *"PULL mode requires an execute_task use case in the container"*. gRPC aborted
> `UNIMPLEMENTED` for the same reason. Both lookups now accept either key, and
> `tests/unit/test_transports_find_the_exec_core.py` pins it **against a real
> `build_app_container()`** — which is the part that was missing: every pre-existing pull and
> gRPC test hand-builds `{"execute_task": …}`, a shape the bootstrap does not produce, so
> mocking the wiring hid the wiring bug.

### 3.2 The loop, and what each knob protects

`run_pull_worker` (`worker_pull/__init__.py:390`) is: register → `lease` → heartbeat → drain →
repeat.

| Setting | Default | What it protects |
|---|---|---|
| `LEASE_MAX_BATCH` | 4 | tasks per lease call |
| `LEASE_WAIT_SECONDS` | 20.0 | long-poll hold; must be ≤ the executor's max |
| `LEASE_IDLE_FLOOR_SECONDS` | 1.0 | **stops a hot spin** |
| `LEASE_ERROR_BACKOFF_SECONDS` | 2.0 | sleep after a transport error |
| `LEASE_RENEW_ENABLED` | **false** | long tasks getting reaped mid-flight |
| `LEASE_RENEW_SAFETY_FRACTION` | 0.5 | renew at half the server's TTL |

**The idle floor is not a nicety** (`worker_pull/__init__.py:472-482`). The long-poll is
best-effort: the executor returns `[]` **immediately** when pull is not enabled there — which
is the default — or when `wait=0`. Without the floor the loop burns a core and floods the
executor with lease + heartbeat requests. The sleep is jittered
(`(idle_floor - elapsed) * (0.5 + random.random())`) so a fleet of pull workers does not
synchronise into a thundering herd.

**Turn renewal on if a task can run longer than the lease TTL.** The interval is *derived*
from the TTL the executor stamps on each lease (`_derive_renew_interval`,
`worker_pull/__init__.py:273-283`), so it is structurally shorter than the TTL and no
SDK-vs-executor misconfiguration is possible.

### 3.3 The batch trap — the subtle one

The executor stamps **one absolute deadline on the whole batch at claim time**, and this SDK
drains the batch **sequentially**. So with `LEASE_MAX_BATCH > 1`, task #4's lease is already
ticking while task #1 runs. If task #1 is slow, #4's lease expires, the executor re-enqueues
it to another worker (**duplicate execution**), and your eventual result is rejected.

That is why renewal spans the batch, not the running task: `_batch_renew_loop`
(`worker_pull/__init__.py:141-194`) renews **every** leased token until the drain finishes.
Each task carries its own token, so they renew independently.

Two behaviours to keep if you ever touch this code:

* A transient renew failure (timeout, 5xx, reset) **raises** and must be retried. Only an
  explicit **409** returns `None` and means the lease is genuinely gone. Treating a network
  blip as a supersede is how you get a duplicate execution.
* A superseded task is **skipped, not run** (`worker_pull/__init__.py:507-515`) — running it
  would burn the full duration for a result whose callback is rejected anyway.

**If you keep `LEASE_MAX_BATCH=1` you avoid the whole class of problem.** Start there; raise
it only after renewal is on and proven.

### 3.4 Several node types on PULL

Same `node_types=[...]` as SERVER — `run_worker` picks the multi-type pull runner when the
container carries a node type registry, and nothing in `main.py` changes:

```python
run_worker(app_settings=app_settings, features_path=..., base_module=...,
           node_types=NODE_TYPES)         # APP_MODE=PULL selects the transport
```

Each node type gets **its own registration** (`endpoint="pull"`, carrying that type's own
schema/icon/ports/kind/functions) and **its own lease loop**, because the executor partitions
the lease queue by `(tenant, worker_type)` — there is no endpoint that leases across types.

The loops run **concurrently**, not round-robin. That matters more than it sounds: polling the
types in turn leaves every other type unpolled for the entire duration of a running task, so
one multi-minute task starves every other node the worker exposes. Concurrency also means each
poll uses the full `LEASE_WAIT_SECONDS` instead of dividing the budget by the number of types.

The heartbeat is its **own task**, not a step inside a lease loop. With N loops there is no
single loop guaranteed to tick — any of them can be busy for minutes — and a pull worker that
stops heartbeating is reaped mid-task by the executor's sweeper.

Everything the single-type loop earned is kept: batch renewal via the shared
`_drain_leased_batch` (so the batch-deadline trap in §3.3 is fixed in exactly one place), the
jittered idle floor, blank-wid re-registration, and deregistration of every type that actually
registered.

Handlers may be sync **or** async, dispatched exactly as the HTTP path does — an `async def`
is awaited, a plain `def` goes to a thread.

> Before this existed, `node_types` in PULL mode were **silently dropped**: the worker
> registered `settings.WORKER_TYPE` instead, leased only that partition, and logged nothing.
> If you are on an older SDK, that is what you are running.

### 3.5 What pull does NOT do for you

Both of these are real gaps in the SDK as of this commit, not configuration:

1. **File references are not resolved.** `process_leased_task`
   (`worker_pull/__init__.py:240-246`) puts `task["payload"]` straight into the command. Your
   handler receives raw `{"__file_ref": true, …}` dicts. See §5.4 for the fix.
2. **Large outputs are not streamed.** The converter is wired only in `routes.py`, so a big
   output rides the callback inline and can hit the broker's ~1 MB cap.

---

## 4. Many node types in one worker

One process, one deployable, several nodes in the workflow builder. Works in **SERVER** and
**PULL** (see §3.4 for what PULL does differently); HEADLESS registers them but has no intake.

### 4.1 The trap, first

> **`node_types=[...]` REPLACES the base single-type registration.** The lifespan registers
> the node types **or** `settings.WORKER_TYPE`, never both
> (`worker_server/__init__.py:144-150`).

So the moment you add a second node type, your **existing** node disappears from the palette
unless you also list it in `NODE_TYPES`. The ms-teams-worker keeps its original webhook node
alive exactly this way — as a thin parity adapter that re-runs the old use case unchanged
(`ms-teams-worker/app/node_types.py`).

### 4.2 Shape

```python
# app/node_types.py
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind

async def _create_meeting(inputs: dict, parameters: dict) -> dict:
    """Handler signature is always (inputs, parameters) -> dict.

    `inputs` are the resolved runtime inputs; `parameters` is the node config.
    Return the outputs dict. RAISE to fail the node — do not return an error
    field and call it success.
    """
    ...
    return {"meeting_url": url, "event_id": event_id}

NODE_TYPES = [
    NodeTypeDefinition(
        worker_type="ms_teams_meeting",     # required, non-blank, globally the node id
        name="Create Teams Meeting",
        description="Creates a Teams meeting via Microsoft Graph.",
        version="0.1.0",                    # build identity — bump on any code change
        spec_version="1.0.0",               # contract identity — bump when schemas/ports change
        node_class="BUSINESS",              # BUSINESS | TECHNICAL
        kind=NodeKind.ACTION,               # trigger|action|read|logic|human|transform|util
        icon="Video", color="#6264A7",
        tags=["teams", "meeting"],
        input_schema=_MEETING_INPUT_SCHEMA,   # auto-form field list (see 4.4)
        output_schema=_MEETING_OUTPUT_SCHEMA,
        handler=_create_meeting,
    ),
    # ... and the original node, or it vanishes
]
```

```python
# main.py
run_worker(app_settings=app_settings,
           features_path=..., base_module=...,
           node_types=NODE_TYPES)
```

`worker_type` blank raises at construction (`node_type_definition.py:__post_init__`). `kind`
must be a `NodeKind` member — an invented value raises, which is deliberate: a `"write"` that
was not in the vocabulary once shipped.

### 4.3 The status-vocabulary trap

A multi-type handler that returns normally is reported as `TaskStatus.SUCCESS.value` —
literally `"success"` (`routes.py:291`). **Not** `"COMPLETED"`. The executor's callback
pipeline treats `status == "success"` as the *only* success signal; anything else is reported
to the control plane as `WORKER_ERROR`. When this was wrong, every multi-type success showed
up as a **failed node with its outputs attached** — which is a confusing way to find out.

You get this for free by returning normally and raising on failure. Do not hand-build status
strings.

### 4.4 Schemas

`input_schema` / `output_schema` are auto-form field lists. Build them with the mapper rather
than hand-writing JSON:

```python
from worker_sdk.layer1_domain.auto_form_mapper import (
    text_field, select_field, switch_field, number_field, array_field,
    required_string, ValidationRule, FieldControl, FieldWrapper, ConditionConfig,
)
```

Full export list in `worker_sdk/layer1_domain/auto_form_mapper/__init__.py`. The FE renders
these directly, so a wrong control type is visible immediately in the builder.

### 4.5 Ports

Default is one input and two outputs — `in_default`, `success`, `failure`
(`value_objects/port.py:default_task_ports`). Override `ports=` only if your node genuinely
branches differently; the default matches the control plane's built-in TASK descriptor, and
drifting from it means the builder draws edges your node does not honour.

### 4.6 Worker functions ≠ node types

`functions=[WorkerFunction(...)]` are **design-time** callables — "Test connection", "list
projects to populate a dropdown". They are exposed at `GET /api/v1/functions` and
`POST /api/v1/functions/{name}/invoke` (`routes.py:472-493`), and are **not** nodes and **not**
part of a run. Handlers are `async def handler(params: dict) -> dict`
(`function_registry.py:invoke`). A function can hang off a node type
(`NodeTypeDefinition.functions`) or off the worker (`run_worker(functions=[...])`).

Available in SERVER and gRPC. **Not in PULL or HEADLESS.**

---

## 5. Reading data — what your handler actually receives

This is where most surprises live, because the control plane does not always send the value;
sometimes it sends a pointer to it.

### 5.1 Three shapes arrive

`HttpFileRefResolver.resolve_inputs` (`http_file_ref_resolver.py:64-129`) handles:

1. **Top-level `file_id` input** — the whole inputs dict is
   `{"file_id": "...", "data_mode": "csv_single"}`. `csv_single` → the first CSV row *becomes*
   your inputs dict; `csv_multi` → `{"data": [ …rows… ]}`. Extra keys (locked defaults) are
   merged on top.
2. **Nested `__file_ref`** — an individual value is `{"__file_ref": true, "file_id": "..."}`.
   Resolved in place, wherever it is nested.
3. **Reconstructable `__file_ref`** — carries an artifact tree (`nested_array` /
   `array_field`) and/or a declared `value_kind`. Rebuilt from the **whole tree**. Checked
   **before** shape 1, because a claim-checked large input also carries a
   `data_mode: "csv_single"` fallback for older workers; letting that win would silently drop
   every array field.

### 5.2 In SERVER mode you get this for free

`routes.py:269` resolves inputs **before** any use case or handler sees them. Your handler
receives real data. You normally never call the resolver yourself.

It only runs when the resolver exists — `FILE_SERVICE_URL` set **and** `RESOLVE_FILE_REFS`
true (`bootstrap.py:98`). Forget `FILE_SERVICE_URL` and your handler silently receives raw ref
dicts instead of data. That is the single most common "why is my input a weird dict" cause.

### 5.3 The diagnostic that saves an afternoon

When a field is missing, the error often lists the envelope keys:

```
Available keys: ['__file_ref', 'file_id', 'arrow_file_id', 'data_mode', …]
```

**Those are envelope keys, not your data's columns.** Seeing them does not mean the file is
corrupt or that Arrow is broken — it means resolution did not happen, or the field genuinely
is not in the data. Compare the resolved row's actual columns against a run that worked; do
not go debugging the storage layer first.

Synthetic columns (`__row_id`, `__parent_row_id`, `_row_index`) are link/grouping keys added
by the CSV split. They are kept during reconstruction and stripped deeply at the end
(`http_file_ref_resolver.py:18-21`). If you see them in your handler, you are looking at raw
rows, not a materialised object.

### 5.4 Pull and gRPC: do it yourself

Neither transport resolves. Ask for the resolver by name and call it:

```python
class ExecuteTaskUseCase:
    def __init__(self, logger, monitor, file_ref_resolver=None, **kwargs):
        self.logger = logger
        self.monitor = monitor
        self._resolver = file_ref_resolver          # injected iff FILE_SERVICE_URL is set

    def execute(self, cmd):
        inputs = cmd.inputs
        if self._resolver is not None:
            inputs = self._resolver.resolve_inputs(inputs)   # sync — you are already off-loop
        ...
```

`resolve_inputs` is synchronous, and the exec core already runs in a thread, so this is safe
exactly here. Do not call it from async code.

If you want to branch without a resolver, the predicates are exported:

```python
from worker_sdk import is_file_ref
from worker_sdk.layer1_domain.value_objects.file_reference import is_file_id_input
```

### 5.5 The memory cap you will hit before you expect to

`RESOLVE_MAX_INPUT_BYTES` (`app_config.py:90`, default **16 MiB**) caps the cumulative **wire**
bytes of one file-backed input collection. Over it, the resolver raises
`FileRefResolutionError` instead of OOMing the pod.

The number is smaller than it looks. The cap counts raw wire bytes, but what stays resident is
**parsed Python objects** — a ~7-byte `{"i":1}` row becomes a ~300-byte dict. That is a **3–40×
blow-up**: 16 MiB of wire is ~48–640 MB resident, worst case. It is sized so the typical 3–5×
case fits an 86 MB-free 256 MB pod.

Raise it with the pod (512 MB → 32 MiB, 1 GB → 64 MiB). Lower it for wide rows. `0` disables
the guard and restores unbounded accumulation — which is how pods OOM.

A handler that genuinely needs a huge collection should consume it in bounded windows rather
than raising the cap. True per-row streaming to the handler does not exist yet.

### 5.6 Writing large outputs

Set `WORKER_CHUNKED_OUTPUT_ENABLED=true` (`app_config.py:106`) and a large list-of-dicts
output field is converted to CSV, uploaded, and replaced by a `file_ref` the control plane
resolves like any stored output — so a 500 MB output never crosses the wire inline
(gRPC ~4 MB, broker ~1 MB) and never OOMs the control plane. Below
`CHUNKING_THRESHOLD_BYTES` (1 MB) it stays inline.

Every uploaded `file_id` is reported back as `_uploaded_file_ids` (`routes.py:252-260`) so the
control plane can garbage-collect the ones nothing ends up pointing at. The worker cannot make
that decision itself.

Streaming is best-effort and **never raises** (`routes.py:229-263`): on any failure it falls
back to the inline value, because on the async path a raise here would kill the background
task with no callback and hang the node.

**SERVER only** — see §3.5.

---

## 6. Testing

```bash
PYTHONPATH=/path/to/worker-sdk python -m pytest tests/ -q
```

**Set `PYTHONPATH` to the worker-sdk source.** Without it you test whatever stale `worker_sdk`
is in site-packages, and your SDK change appears to do nothing.

What to cover, in priority order:

1. **The handler, directly** — `handler(inputs, parameters)` is a plain callable. No HTTP, no
   container. This is most of your value.
2. **Both execute paths** if you touched anything in the request path. `/execute` passing
   proves little about `/execute-async`.
3. **Failure**, not just success. For multi-type, assert that a raising handler yields
   `status == "error"` and that a normal return yields `"success"` — the vocabulary trap in
   §4.3 is invisible until it reaches the executor.
4. **Resolution off.** Construct with `file_ref_resolver=None` and assert something sane. That
   is production for a pull worker and for any environment missing `FILE_SERVICE_URL`.

---

## 7. Shipping

**A worker-sdk-only change does not rebuild your worker.** CI matches changed files against a
service's own directory by prefix, so an SDK fix ships and no worker runs it. To pick up an SDK
change you must touch a file inside the worker's own directory — the convention is a dated
comment at the bottom of `main.py` saying which SDK change you are pulling in and why. Every
worker in this repo has several; copy the shape.

Pre-flight:

- [ ] `WORKER_TYPE` set and non-blank (blank is fatal, by design)
- [ ] `PROXY_URL` is the **externally reachable** URL — SERVER only; the default
      `http://SERVER_HOST:SERVER_PORT` is a local-dev fallback that registers an unreachable
      endpoint in the cloud
- [ ] `REGISTRY_URL` points at the executor
- [ ] `FILE_SERVICE_URL` set, or you have accepted that inputs arrive unresolved
- [ ] adding a second node type? the **original** one is still in `NODE_TYPES`
- [ ] `MAX_WORKER_THREADS` matches the pod (256 MB → 8, 512 MB → 16, 1 GB → 32)
- [ ] `WORKER_MAX_CONCURRENT` set if you want 429 backpressure instead of overload
- [ ] PULL: `LEASE_RENEW_ENABLED=true` if any task can outlive the TTL, and
      `LEASE_MAX_BATCH=1` until it is proven

---

## 8. Where to look when it misbehaves

| Symptom | Look at |
|---|---|
| Node not in the palette | did `node_types` replace your base registration? (§4.1) |
| Every multi-type run "fails" but has outputs | status vocabulary (§4.3) |
| Input is a dict with `__file_ref` | no resolver: `FILE_SERVICE_URL` unset, or PULL/gRPC (§5.2, §5.4) |
| `Available keys: ['__file_ref', …]` | envelope keys, not columns (§5.3) |
| Worker OOMs on a big input | `RESOLVE_MAX_INPUT_BYTES` and the 3–40× parse blow-up (§5.5) |
| Task ran twice | PULL lease expired: renewal off, or batch drain (§3.3) |
| Pull worker pegs a CPU when idle | `LEASE_IDLE_FLOOR_SECONDS` (§3.2) |
| `RuntimeError: … called ON an asyncio event loop` | you called `execute()` from async (§1) |
| Node hangs forever, no error | callback never landed — check worker logs for `_post_callback` (§2.2) |
| Registered but never gets work | HEADLESS has no intake (§0) |
| On PULL only one node type appears | an SDK older than §3.4 — it dropped `node_types` silently |
| SDK change has no effect | stale `site-packages` locally (§6) or no rebuild marker (§7) |

## 9. Appendix — a complete worker in four files

Copy-pasteable. Runs in **push** and **pull** with only an env change, resolves file
references in both, and grows into multi-node by adding one file.

```
my-worker/
├── main.py
├── app/
│   ├── layer4_frameworks/config.py
│   └── layer2_application/features/execute_task/
│       ├── __init__.py
│       └── use_cases/execute_task_usecase.py
└── tests/unit/test_execute_task_usecase.py
```

**`app/layer4_frameworks/config.py`** — subclass, do not edit the SDK's:

```python
from worker_sdk import Settings as SdkSettings


class Settings(SdkSettings):
    APP_NAME: str = "My Worker"
    WORKER_TYPE: str = "my_worker"          # the node id; blank is fatal
    WORKER_NAME: str = "My Worker"
    WORKER_DESCRIPTION: str = "Does the thing."
    WORKER_NODE_CLASS: str = "BUSINESS"
    SERVER_PORT: int = 35010
    MY_UPSTREAM_URL: str = ""               # your own field: read off `settings`, not the SDK singleton


settings = Settings()
```

**`app/.../execute_task/__init__.py`** — discovery is by convention (`bootstrap.py:38-48`);
the name must be exactly this or the worker starts and answers *"No executor available"*:

```python
from .use_cases.execute_task_usecase import ExecuteTaskUseCase

__all__ = ["ExecuteTaskUseCase"]
```

**`app/.../execute_task/use_cases/execute_task_usecase.py`**:

```python
from typing import Any

from worker_sdk import ExecuteTaskCommand, ExecuteTaskResult, TaskStatus

MY_INPUT_SCHEMA = [
    {"key": "customer_id", "type": "string", "required": True, "label": "Customer ID"},
]
MY_OUTPUT_SCHEMA = [
    {"key": "name", "type": "string", "label": "Customer name"},
]


class ExecuteTaskUseCase:
    def __init__(self, logger, monitor, file_ref_resolver=None, **kwargs: Any) -> None:
        # Injected BY NAME (bootstrap.py:120-127). `file_ref_resolver` exists only when
        # FILE_SERVICE_URL is set and RESOLVE_FILE_REFS is true — hence the default.
        # Keep **kwargs: the SDK may offer dependencies you did not ask for.
        self.logger = logger
        self.monitor = monitor
        self._resolver = file_ref_resolver

    def execute(self, cmd: ExecuteTaskCommand) -> ExecuteTaskResult:
        # SYNC on purpose. The SDK guarantees this runs off the event loop and refuses
        # to run otherwise (assert_off_event_loop). Blocking here is allowed.
        inputs = cmd.inputs

        # SERVER already resolved these (routes.py:269). PULL and gRPC did NOT, so
        # resolve defensively: it is idempotent, and this is the ONE place it is safe
        # to call the sync resolver.
        if self._resolver is not None:
            inputs = self._resolver.resolve_inputs(inputs)

        customer_id = inputs.get("customer_id")
        if not customer_id:
            # Decline with a reason rather than inventing a default.
            return ExecuteTaskResult(
                task_id=cmd.task_id, status=TaskStatus.ERROR,
                error="customer_id is required",
            )

        name = self._lookup(customer_id, cmd.parameters)
        self.logger.info("Looked up customer", task_id=cmd.task_id, customer_id=customer_id)
        return ExecuteTaskResult(
            task_id=cmd.task_id, status=TaskStatus.SUCCESS, outputs={"name": name},
        )

    def _lookup(self, customer_id: str, parameters: dict) -> str:
        import httpx  # sync client — correct here, we are off the loop
        from app.layer4_frameworks.config import settings

        resp = httpx.get(f"{settings.MY_UPSTREAM_URL}/customers/{customer_id}", timeout=30.0)
        resp.raise_for_status()          # raising is fine: the SDK turns it into an ERROR result
        return resp.json()["name"]
```

**`main.py`** — identical for both modes:

```python
import os

from worker_sdk import run_worker

from app.layer4_frameworks.config import settings as app_settings
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MY_INPUT_SCHEMA, MY_OUTPUT_SCHEMA,
)

if __name__ == "__main__":
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(os.path.dirname(__file__),
                                   "app", "layer2_application", "features"),
        base_module="app.layer2_application.features",
        extra_dependencies={
            "input_schema": MY_INPUT_SCHEMA,
            "output_schema": MY_OUTPUT_SCHEMA,
        },
    )
```

**Run it — push:**

```bash
APP_MODE=SERVER \
REGISTRY_URL=http://localhost:8000 \
PROXY_URL=http://host.docker.internal:35010 \
FILE_SERVICE_URL=http://localhost:8010 \
MY_UPSTREAM_URL=http://localhost:9000 \
python main.py
```

`PROXY_URL` must be reachable **from the executor**. The default
`http://SERVER_HOST:SERVER_PORT` is a local-dev fallback and registers an unreachable endpoint
anywhere else — the worker looks healthy and never receives a task.

**Run it — pull** (no route needed, no `PROXY_URL`):

```bash
APP_MODE=PULL \
REGISTRY_URL=https://<executor> \
WORKER_TENANT=<tenant> \
FILE_SERVICE_URL=http://localhost:8010 \
LEASE_MAX_BATCH=1 \
LEASE_RENEW_ENABLED=true \
MY_UPSTREAM_URL=http://localhost:9000 \
python main.py
```

**Smoke-test push without the platform:**

```bash
curl -s localhost:35010/api/v1/info | head -c 400
curl -s -X POST localhost:35010/api/v1/execute -H 'Content-Type: application/json' \
  -d '{"task_id":"t1","action":"execute","inputs":{"customer_id":"C1"},"parameters":{}}'
```

**Test — no HTTP, no container:**

```python
from unittest.mock import MagicMock
from worker_sdk import ExecuteTaskCommand, TaskStatus
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)


def test_missing_customer_id_is_declined_not_guessed():
    uc = ExecuteTaskUseCase(logger=MagicMock(), monitor=MagicMock())
    res = uc.execute(ExecuteTaskCommand(task_id="t1", action="execute", inputs={}))
    assert res.status == TaskStatus.ERROR and "customer_id" in res.error


def test_it_resolves_file_refs_when_no_transport_did():
    """Production for a PULL worker: the input arrives as a raw ref."""
    resolver = MagicMock()
    resolver.resolve_inputs.return_value = {"customer_id": "C1"}
    uc = ExecuteTaskUseCase(logger=MagicMock(), monitor=MagicMock(),
                            file_ref_resolver=resolver)
    uc._lookup = lambda cid, params: "Acme"
    res = uc.execute(ExecuteTaskCommand(
        task_id="t1", action="execute",
        inputs={"customer_id": {"__file_ref": True, "file_id": "f1"}},
    ))
    assert res.status == TaskStatus.SUCCESS and res.outputs == {"name": "Acme"}
```

**To grow into multi-node**: add `app/node_types.py` with a `NODE_TYPES` list and pass
`node_types=NODE_TYPES` to `run_worker` — then re-read §4.1, because that call **removes** the
single-type registration above. Works in SERVER and PULL alike — the entry point does not
change.

---

## 10. Read the real ones

| Worker | Shows |
|---|---|
| `ms-teams-worker` | multi-type done properly, incl. keeping the original node alive |
| `mapping-data-worker` | multi-type + heavy data handling |
| `jira-worker` | multi-type + worker functions for design-time pickers |
| `gateway-worker` | a functions-only worker |
| `http-request-worker` | the simplest single-type worker |
