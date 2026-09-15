# Sub 4.5 — Decision detail

> Companion to [`phase-4-sub-4.5-orchestrator-split.md`](./phase-4-sub-4.5-orchestrator-split.md).
> The PR plan covers *what* and *how*; this doc explains the
> source-code and design impact of the three decisions waiting on
> stakeholder approval, with concrete file references so reviewers
> can judge cost and lock-in before they sign off.

## Decision 1 — split shape (HTTP-only control plane vs. control-flow orchestrator)

### What the boundary looks like in code

The proposed split draws a single seam through `bootstrap.py`. Each
side of the seam is a list of components that stay or move. The
following audit is against the post-Sub-4.4 codebase.

**Stays on control plane (HTTP-facing):**

| Component | Source | Why |
|---|---|---|
| FastAPI app + routes | `app/layer3_adapters/controllers/restful/v1/` | HTTP terminator |
| Socket.IO server | `main.py:14-84` | Real-time push to browser; needs the per-instance Redis adapter |
| `RealtimeBroadcastingPublisher` | `app/layer4_frameworks/realtime/broadcasting_publisher.py` | Decorates the publish chain to fan events to Socket.IO rooms; only useful where SIO server lives |
| `SocketIORealtimeConsumer` | `app/layer4_frameworks/realtime/socketio_consumer.py` | Subscribes to broker, emits to SIO; co-located with SIO |
| `PushGatewayRealtimeConsumer` | `app/layer4_frameworks/realtime/push_gateway_consumer.py` | Mobile push fan-out; HTTP-tier concern |
| `CachedProjectorRunContextViewStore` | `app/layer4_frameworks/run_context/cached_projector_view_store.py` | Backs HTTP `GET /runs/{id}` reads; co-locating with the API serves read-after-write inside one round-trip |
| `OrchestrationWorker` (filtered) | `app/layer4_frameworks/orchestration_queue/worker.py` | Consumes only `EditNodeDataAction` so the HTTP caller's `correlation_id` future resolves on the same pod |
| `ResponseRegistry` + `ResponseTopicListener` | `app/layer4_frameworks/orchestration_queue/{response_registry,response_topic_listener}.py` | M.3 sticky routing — futures are pod-local |
| All 56 use-case classes | `app/layer2_application/features/*/use_cases/` | They run inside HTTP route handlers |
| Outermost publisher chain (`EventLogAppending → RealtimeBroadcasting → Enriching`) | `bootstrap.py:300-330` | Emits to broker; consumed by orchestrator |

**Moves to orchestrator:**

| Component | Source | Why |
|---|---|---|
| `OrchestrationEngine` | `app/layer1_domain/services/orchestration_engine.py` | Holds task creation, dispatch, child-run start; consumed by event-driven flow |
| `NodeHandlerRegistry` + 17 handler classes | `app/layer1_domain/services/node_handlers/` | Invoked by `ExecutorConsumer`; not used on HTTP path |
| `DispatcherConsumer` | `app/layer4_frameworks/dispatcher/dispatcher_consumer.py` | Computes fan-out from `NodeOutputProduced` |
| `TaskCreatorConsumer` | `app/layer4_frameworks/executor/task_creator_consumer.py` | Sub 4.1 — creates Task entities from `NodeInput` |
| `RunStartConsumer` | `app/layer4_frameworks/executor/run_start_consumer.py` | Sub 4.2 — dispatches entry nodes from `RunStarted` |
| `ExecutorConsumer` | `app/layer4_frameworks/executor/executor_consumer.py` | Sub 3.5 — invokes handlers |
| `OutboxDispatcher` + handlers | `app/layer4_frameworks/outbox/` | Sub 3.2 — performs deferred side effects |
| `OrchestrationWorker` (filtered) | same as above | Consumes everything except `EditNodeDataAction` |
| `EventDrivenLoopDriver` | `app/layer1_domain/services/loop_driver/` | Loop iteration coordination |
| `SuccessorProcessor`, `TaskFactory`, `VariableResolver`, `DataStoreService` | `app/layer1_domain/services/` | Engine internals |

**Shared (both pods build their own instance from the same factory):**

| Component | Source | Notes |
|---|---|---|
| HANA / Event Mesh / Mock infra | `app/layer4_frameworks/` factory | Each pod opens its own connection pool |
| All repos (`run`, `task`, `workflow`, `stage_history`, `event_log`, `snapshot`) | `app/layer4_frameworks/persistence/` | Stateless reads; backed by HANA |
| `FileServiceClient`, `SchemaTransformClient`, `DataFactoryClient` | `app/layer4_frameworks/clients/` | Stateless HTTP clients |
| `BrokerOrchestrationQueue` | `app/layer4_frameworks/orchestration_queue/kafka_queue.py` | The same queue is consumed from both pods with `accepted_actions` filter |

### Use cases: 13 hold a direct engine reference

```
cancel_run, complete_task, edit_node_data, fail_task, pause_run,
rerun_run, rerun_task, resume_run, resume_pending_task, start_run,
sweep_timed_out_child_runs, test_full_workflow, test_node, trigger_node
```

What they actually call on the engine breaks into two groups:

**Group A — `emit_*` methods only** (12 of 13). These are thin
wrappers around `event_publisher.publish(topic, event_dataclass)`:

```python
# start_run_use_case.py:207
self._engine.emit_run_started(run)
# resume_pending_task_use_case.py:335
self._orchestration_engine.emit_task_dispatched(task, run, node)
# fail_task_use_case.py:130
self._engine.emit_task_failed(...)
```

**Code change for Group A**: the `IOrchestrationEngine` interface
already exists. Today it leaks more than it should; we slim it to an
`IDomainEventEmitter` interface (~25 `emit_*` methods, no `orchestrate_*`).
Control plane gets a thin emitter implementation that takes only the
publisher; orchestrator gets the full engine. **~120 LoC** — drop
methods from the interface, build a separate emitter class that
implements just the emit half.

**Group B — synchronous `orchestrate_*` calls** (2 of 13):

```python
# edit_node_data_use_case.py:939
await self._engine.orchestrate_fail(run.id, task.id)
# test_full_workflow_use_case.py:150
await self._engine.orchestrate_start(test_run.id)
```

**Code change for Group B**: replace each with a queue enqueue —
both methods already have action equivalents (`FailTaskAction`,
`StartRunAction`). The `start_run` use case already does this at
`start_run_use_case.py:222` (`enqueue(StartRunAction(run_id=run.id))`);
`test_full_workflow` should follow the same pattern, and
`edit_node_data` should `enqueue(FailTaskAction(...))`. **~10 LoC.**

### Design impact

- **Use cases become "thin emitters."** The split forces a clean
  domain-events boundary that's already mostly there. The two
  remaining synchronous calls are the only non-event-driven leaks.
- **`IOrchestrationEngine` shrinks to two interfaces.** One for HTTP
  callers (emit-only); one for the queue worker (full engine).
  Removes the subtle invitation for new use cases to add
  `engine.do_some_synchronous_thing()` calls — under the split, the
  only thing they CAN call is emit.
- **Read-after-write semantics preserved.** The projector stays on
  the control plane, so `POST /runs` followed by `GET /runs/{id}`
  reads its own write through the projector — the broker round-trip
  to the orchestrator does not gate the HTTP read because the
  control-plane projector also subscribes to the events.

### Reversibility

The split is reversible by reverting bootstrap to the unified
builder; the consumer/publisher logic survives unchanged. The
interface narrowing (`IOrchestrationEngine` → `IDomainEventEmitter`)
is the only one-way move — going back would require restoring the
methods, but that's a 30-line revert.

### Lock-in

| Decision lock | Severity | Mitigation |
|---|---|---|
| Use cases can no longer call `engine.orchestrate_*` synchronously from HTTP | Low | The two existing call sites already have queue-action equivalents; new code paths get a clean error message at construction (`'orchestrate_start' missing on emitter`) |
| Cross-pod broker becomes load-bearing for run progression | Medium | Already true under `EXECUTOR_MODE=executor`; Sub 4.5 doesn't introduce a new dependency, only hardens the existing one |
| HTTP `POST /runs` returns before the orchestrator has dispatched entry nodes | Low | Already true — `start_run` returns after enqueueing `StartRunAction`; ETA from enqueue to first NodeInput is ~10ms in production |

---

## Decision 2 — orchestration queue split (`EditNodeDataAction` on control plane, others on orchestrator)

### What the queue looks like today

`OrchestrationWorker._dispatch` switch-case at
`app/layer4_frameworks/orchestration_queue/worker.py:82-110`:

```python
if isinstance(action, StartRunAction):
    await self._engine.orchestrate_start(action.run_id)
elif isinstance(action, ContinueRunAction):
    await self._engine.orchestrate_continue(...)
elif isinstance(action, FailTaskAction):
    await self._engine.orchestrate_fail(...)
elif isinstance(action, ResumeTaskAction):
    await self._engine.orchestrate_resume(...)
elif isinstance(action, ExecuteNodeAction):
    await self._engine.orchestrate_execute_node(...)
elif isinstance(action, LoopIterationAction):
    await self._engine.orchestrate_loop_iteration(...)
elif isinstance(action, ResumeRunAction):
    await self._engine.orchestrate_resume_run(...)
elif isinstance(action, EditNodeDataAction):
    await self._edit_node_data_use_case.execute(...)
    # ... result published to per-instance response topic
```

Today every action is consumed by a single `OrchestrationWorker` on
the control plane.

### Code change for the split

**The 4-line filter on `OrchestrationWorker`:**

```python
# worker.py
class OrchestrationWorker:
    def __init__(
        self, ...,
        accepted_actions: set[type] | None = None,
    ) -> None:
        ...
        self._accepted_actions = accepted_actions

    async def _dispatch(self, action: Any) -> None:
        if (
            self._accepted_actions is not None
            and type(action) not in self._accepted_actions
        ):
            return  # different pod owns this type
        ...  # existing switch-case
```

**Bootstrap wires two workers** (one per role):

```python
# control_plane.py
control_plane_worker = OrchestrationWorker(
    queue=orchestration_queue,
    engine=None,  # no engine on this pod
    accepted_actions={EditNodeDataAction},
    edit_node_data_use_case=container["edit_node_data"],
    response_registry=orchestration_response_registry,
    instance_id=instance_id,
    broker_client=broker,
)

# orchestrator.py
orchestrator_worker = OrchestrationWorker(
    queue=orchestration_queue,
    engine=orchestration_engine,
    accepted_actions={
        StartRunAction, ContinueRunAction, FailTaskAction,
        ResumeTaskAction, ResumeRunAction, ExecuteNodeAction,
        LoopIterationAction,
    },
)
```

### Design impact

- **`EditNodeDataAction` is the only action that touches HTTP-side
  state.** The PATCH `/data` HTTP handler awaits a future on
  `orchestration_response_registry`; that future MUST be resolved on
  the same pod that holds it. M.3's per-instance response topic
  already encodes this — keeping `EditNodeDataAction` on the control
  plane preserves the existing routing without changes.
- **The orchestrator's worker becomes purely "control-flow forward
  motion."** Every action it consumes advances the run (start,
  continue, fail, resume, execute_node, loop_iteration). No HTTP
  back-channel.
- **Action-type partitioning is enforced by code, not by topic.**
  The Kafka consumer-group still has all actions on one topic; the
  filter is per-pod. Alternative considered: split into two queues.
  Rejected — same-topic + filter is simpler and matches the existing
  shape.

### Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| Engineer adds new action type (e.g. `RetryFailedTaskAction`) and forgets to update either filter set | Action is consumed by no pod; producer's enqueue silently goes nowhere | CI test asserts `union(control_plane.accepted, orchestrator.accepted) == ALL_ACTION_TYPES` |
| Operator misconfigures `APP_ROLE` so both pods are `combined` | `EditNodeDataAction` consumed by both → futures double-resolve | Each `combined`-mode pod has full coverage; the broker-side consumer-group ensures a single delivery per action regardless of pod count |
| Control plane is down — `EditNodeDataAction` accumulates in queue | PATCH calls return 504 from the HTTP handler before they reach the queue worker, so operator alerts fire on HTTP errors. Queued actions drain when the pod comes back. | Standard k8s liveness/readiness probes |

### Reversibility

Set `accepted_actions=None` on either worker — both consume
everything, behaviour matches pre-split. One-line revert.

### Lock-in

| Decision lock | Severity | Mitigation |
|---|---|---|
| Control plane permanently owns `EditNodeDataAction` | Low | Sticky to the HTTP correlation-id pattern; if PATCH ever needs to scale beyond a single control-plane replica, M.3's per-instance response topic already partitions by `INSTANCE_ID` |
| Adding a new action type requires updating both filter sets | Low | The CI test forces this discipline; dev cost is one config line |

---

## Decision 3 — rollout gates with `APP_ROLE={control_plane, orchestrator, combined}` default `combined`

### What the bootstrap split looks like

**File layout** (proposed):

```
workflow-control-plane-service/
  bootstrap/
    __init__.py             # build_app_container (combined-mode shim)
    shared.py               # _build_infrastructure(): repos, broker, event_log,
                            #   snapshot_repo, view_store
    control_plane.py        # build_control_plane_container()
    orchestrator.py         # build_orchestrator_container()
  main.py                   # APP_ROLE-aware entry; FastAPI for control_plane
                            #   or combined; orchestrator delegates to
                            #   app/orchestrator_main.py
  app/
    orchestrator_main.py    # renamed from executor_main.py; absorbs
                            #   the migrated consumers
```

**`bootstrap/__init__.py`** (combined-mode shim, ~30 LoC):

```python
from .control_plane import build_control_plane_container
from .orchestrator import build_orchestrator_container
from .shared import build_infrastructure

def build_app_container(sio_server=None) -> dict:
    """Combined-mode bootstrap — preserves single-pod behaviour.

    Composes both role containers over a single shared infrastructure
    instance. Used by tests, dev environments, and any deployment
    that has not migrated to the role split.
    """
    infra = build_infrastructure()
    cp = build_control_plane_container(sio_server=sio_server, infra=infra)
    orch = build_orchestrator_container(infra=infra)
    # Composition invariant: combined = control_plane ∪ orchestrator.
    # No key collisions allowed — CI test enforces this.
    return {**cp, **orch}
```

**`main.py`** (~15 LoC change):

```python
if settings.APP_ROLE == "control_plane":
    container = build_control_plane_container(sio_server=sio_server)
elif settings.APP_ROLE == "orchestrator":
    # Hand off to orchestrator entry point.
    sys.exit(orchestrator_main.main())
else:  # combined (default)
    container = build_app_container(sio_server=sio_server)
```

### Code change for the soak

The migration is built so each gate is an additive PR with no
production behaviour change until the final flip:

| Gate | What ships | What changes in prod | Rollback |
|---|---|---|---|
| **0** | `EXECUTOR_MODE=executor` default (Sub 4.4) | Handler invocation moves to wio-executor pod; orchestrator-side consumers still in control-plane process | Set `EXECUTOR_MODE=in_process` in env |
| **1** | Subs 4.5.1–4.5.3 (broker migration of three tees) | Three consumers now subscribe to broker instead of hooking publisher chain. Both still co-located in `combined` mode. | Revert PRs |
| **2** | Sub 4.5.4 (bootstrap split) + Sub 4.5.5 (deployment artefacts) | None — `APP_ROLE=combined` is default. New pod manifest exists but is not deployed yet. | Don't deploy the orchestrator pod |
| **3** | Production flip — deploy `wio-orchestrator` pod, set control plane to `APP_ROLE=control_plane` | Two pods running; events traverse broker between them | Set both back to `APP_ROLE=combined`; orchestrator pod becomes redundant |
| **4** | Cleanup PR (post Gate 3 stable for 4 weeks) | Delete `combined` mode + `bootstrap/__init__.py` shim | One-PR revert |

Gate 0 → Gate 4 spans approximately 7 weeks (per the soak windows in
the master PR plan §9). During Gates 1–2, prod is on the unified
single-pod topology — the only thing different is the wiring.

### Design impact of `combined` mode

Keeping `combined` mode as the default is the most consequential
design decision in Sub 4.5 — it determines how long we live with
two parallel topologies.

**Pros of permanent `combined` retention:**
- Dev environments and integration tests stay simple (one Python
  process, one bootstrap call).
- Single-pod deployments (small customer installs, on-prem) keep
  working without ops effort.
- Composition invariant (`combined = control_plane ∪ orchestrator`)
  is enforceable via a CI test that asserts no key collisions
  between the two builders.

**Cons of permanent `combined` retention:**
- Three code paths instead of two — every bootstrap change must work
  in `combined` even when `combined` is rare in production. Drift
  risk grows over time.
- A subtle "co-located fast path" can sneak in. Example: a
  consumer that falls back to direct in-process call when
  `combined` is detected would bypass broker semantics and cause
  drift between modes.

**Recommendation:** keep `combined` mode permanently for dev/test,
but enforce the composition invariant via CI. The drift risk is
manageable because `combined` is just a builder composition, not a
behavioural alternative.

### Reversibility per gate

- **Gates 0–2 are individually revertible** — each is an additive
  config or wiring change.
- **Gate 3 is the irreversible point.** Once the orchestrator pod
  is in production and consuming the queue + node-input topic, a
  rollback to single-pod requires either (a) downscaling the
  orchestrator to zero replicas while flipping control plane back to
  `combined`, or (b) tolerating the topology for the time it takes
  to revert. (a) is the documented rollback path in the deployment
  manifest's runbook.
- **Gate 4 is one-way.** Deleting `combined` mode means a future
  rollback requires restoring those ~30 LoC of shim. Acceptable —
  it's a chemo cleanup, not a deletion of unique behaviour.

### Source-code ripple

Sub 4.5 touches a focused set of files. Estimate by category:

| Category | Files touched | LoC delta |
|---|---|---|
| Three consumer migrations (4.5.1/2/3) | `dispatcher_consumer.py`, `task_creator_consumer.py`, `run_start_consumer.py`, `bootstrap.py` | ~+200 / -150 (delete tee classes) |
| Bootstrap split (4.5.4) | `bootstrap.py` → `bootstrap/{__init__,shared,control_plane,orchestrator}.py` | ~+450 (mostly relocated existing code; +200 new shim/composition logic) |
| `OrchestrationWorker` filter (Decision 2) | `worker.py` | +15 / -0 |
| Use-case engine reference narrowing (Decision 1) | 13 use-case files + `IOrchestrationEngine` interface | ~+50 / -50 (substituting interface type, no behaviour change) |
| `main.py` role branching (Decision 3) | `main.py`, `app/orchestrator_main.py` (rename + extend) | +50 / 0 |
| Deployment artefacts (4.5.5) | `Dockerfile.executor` → `Dockerfile.orchestrator`, manifests, k8s yaml | ~+80 |
| Tests | ~30 files updated; ~15 new files | ~+800 / -200 |
| **Total** | **~75 files** | **+1645 / -400 (net ~+1245)** |

The "net LoC" is higher than the §12 estimate (~970) because the
test surface is wider than originally scoped — the broker-migration
tests need new fixtures that simulate two-pod topology in-process.

### Lock-in

| Decision lock | Severity | Mitigation |
|---|---|---|
| Production runs with two pods after Gate 3 | High but expected | The whole point of the migration; rollback is to scale orchestrator to 0 + flip control plane to `combined` |
| `APP_ROLE` becomes part of the deployment contract | Medium | Already a one-line env-var change in CF/k8s manifests; documented in the operator runbook |
| Shared HANA / broker / event-log infrastructure assumes both pods can reach them with low latency | Medium | Already a constraint today (worker-executor service has the same requirement); no new dependency |

### One implementation detail worth highlighting

The composition invariant — `combined = control_plane ∪ orchestrator`
— is enforceable as a unit test:

```python
def test_combined_is_union_of_role_containers():
    cp = build_control_plane_container()
    orch = build_orchestrator_container()
    combined = build_app_container()

    cp_keys = set(cp.keys())
    orch_keys = set(orch.keys())
    combined_keys = set(combined.keys())

    # No key collision between roles.
    overlap = cp_keys & orch_keys
    assert overlap <= {"_infra", "event_log_repo", "monitor"}, \
        f"control_plane and orchestrator both built: {overlap}"

    # Combined sees every role's keys.
    assert cp_keys <= combined_keys
    assert orch_keys <= combined_keys
```

This test guards against drift: if a future PR adds a new use case
to `build_orchestrator_container` and forgets to expose it in
`combined`, CI fails. ~20 LoC.

---

## Summary table — what each decision actually costs

| Decision | LoC delta | Risk | Reversibility | Lock-in concern |
|---|---|---|---|---|
| **1** Split shape | +120 emitter interface, +15 use-case migrations, ~+450 bootstrap relocation | Medium — interface narrowing is one-way | High through Gate 2; medium at Gate 3 | Use cases can no longer call `engine.orchestrate_*` synchronously |
| **2** Queue split | +15 worker filter, +30 bootstrap config | Low | Trivial — filter is `accepted_actions=None` to revert | None — filter is purely additive |
| **3** Rollout gates with `combined` default | +50 `main.py` branching, +30 shim, +20 CI test | Low through Gate 2; medium at Gate 3 | Per-gate revertible until Gate 3; one-way at Gate 4 | `combined` mode permanence vs. retire-after-soak |

## What I'd push back on if I were the reviewer

1. **The interface narrowing in Decision 1** isn't strictly required
   for the split to work. We could keep `IOrchestrationEngine` as-is
   and just inject a thin engine on the control plane that raises on
   the unused methods. Pro: smaller PR. Con: misses the chance to
   make the boundary explicit in the type system.
2. **`combined` mode permanence in Decision 3** is a slow-burn debt.
   If we keep it forever, every bootstrap change has to consider
   three topologies. Alternative: retire `combined` 4 weeks after
   Gate 3 ships, leave dev/test on whatever role the test fixture
   wires explicitly.
3. **Sub 4.5's scope might over-deliver.** Decisions 1 and 2 could
   ship without decision 3 (skip the bootstrap split, keep one
   process). The question is whether you want the operational
   benefit of two pods now, or just the "events drive everything"
   correctness benefit. The latter is already complete after Sub 4.4.

If you want me to recommend a smaller scope: ship just the broker
migration (Subs 4.5.1–4.5.3), defer the bootstrap split until the
operational signal demands it. The system is fully event-driven
either way.
