# Phase 4 Sub 4.5 — Orchestrator extraction (PR plan)

> **Status: PROPOSAL.** Document-only sketch for stakeholder review.
> Pairs with [`phase-3-executor-extraction.md`](./phase-3-executor-extraction.md)
> and [`phase-3-verification.md`](./phase-3-verification.md). No code
> changes accompany this doc.

## 0. TL;DR

Sub 4.5 migrates the three publisher-tee consumers
(`DispatcherConsumer`, `TaskCreatorConsumer`, `RunStartConsumer`)
from in-process publisher hooks to broker-subscribed consumers, then
splits `bootstrap.py` into two binaries:

| Binary | Responsibility |
|---|---|
| **wio-control-plane** (`main.py`) | HTTP/REST + Socket.IO + Projector (read side) + use cases that respond to HTTP |
| **wio-orchestrator** (`app/executor_main.py`, renamed) | Dispatcher + TaskCreator + RunStart + Executor + Outbox + (optionally) OrchestrationWorker for `ExecuteNodeAction` |

After Sub 4.5 the control plane is a pure HTTP front end; everything
that consumes events for forward control flow lives on the orchestrator
pod. The worker-executor service is **untouched** — it stays as the
task-RPC bridge to external workers.

**Estimated cost.** ~600 LoC (refactor, not new code) + bootstrap
split + integration-test fixtures + deployment manifests. 4-week
soak window post-merge before flipping production traffic to the
split topology.

## 1. Why split now

After Sub 4.4 lands (`EXECUTOR_MODE=executor` default), every node
handler invocation already runs on the wio-executor pod. The control
plane still hosts:

1. The three publisher-tee consumers (Dispatcher, TaskCreator,
   RunStart) — running in-process because the tee pattern requires
   shared publisher chain access.
2. The orchestration queue worker (`OrchestrationWorker`) consuming
   `ExecuteNodeAction` and `EditNodeDataAction`.
3. The outbox dispatcher (Sub 3.2).
4. The Projector (read-side; folds events into the view store).
5. HTTP/REST + Socket.IO server.

Items 1, 2, 3 are control-flow concerns — they advance the workflow
forward in response to events. Items 4 and 5 are HTTP-facing
concerns. Mixing both in one binary means:

- A handler-tier scaling event (e.g. a thundering-herd of
  `NodeInput` envelopes) saturates the HTTP loop and degrades API
  responsiveness.
- Control-plane redeploys (e.g. a UI bug fix) cycle the entire
  orchestration substrate.
- Operators can't observe the two concerns separately —
  orchestration backlogs and HTTP errors share a metrics namespace.

The split addresses all three at once.

## 2. Non-goals

- **Replacing the worker-executor service.** Task RPC is its own
  concern; Sub 4.5 doesn't touch it.
- **Splitting the projector out.** Today the projector folds events
  into a view-store the HTTP handlers read; moving it would break
  read-after-write for HTTP. Defer to a future "read-replica
  projector" PR if HTTP scale demands it.
- **Replacing the orchestration queue with bus-only routing.** PATCH
  `/data` continues through `EditNodeDataAction`; Sub 4.5 just
  decides which pod consumes it (see §6).
- **Cross-pod transactional state.** Each pod operates on the shared
  event log + HANA repos; consistency comes from event-log dedupe +
  the projector's existing fold semantics, not from distributed
  transactions.

## 3. Architecture before / after

### Before Sub 4.5 (post Sub 4.4)

```
┌────────────────────────────────────────┐    ┌──────────────────┐
│        wio-control-plane pod            │    │  wio-executor    │
│                                         │    │      pod          │
│  ┌──────────────────────────────────┐   │    │ ┌─────────────┐ │
│  │ FastAPI / Socket.IO              │   │    │ │ Executor    │ │
│  └──────────────────────────────────┘   │    │ │ Consumer    │ │
│  ┌──────────────────────────────────┐   │    │ └─────────────┘ │
│  │ OrchestrationWorker (queue)      │   │    │ ┌─────────────┐ │
│  └──────────────────────────────────┘   │    │ │ Outbox      │ │
│  ┌──────────────────────────────────┐   │    │ │ Dispatcher  │ │
│  │ Publisher chain (in-process)     │   │    │ └─────────────┘ │
│  │   ┌─ DispatcherTee   ─┐          │   │    │                 │
│  │   ├─ TaskCreatorTee  ─┤          │   │    │                 │
│  │   ├─ RunStartTee     ─┤          │   │    │                 │
│  │   ├─ EventLogAppend  ─┤          │   │    │                 │
│  │   └─ Realtime        ─┘          │◀──│────┤  broker         │
│  │                       │          │   │    │  (Event Mesh)   │
│  │                       ▼          │   │    │                 │
│  │                    broker        │───│───▶│                 │
│  └──────────────────────────────────┘   │    │                 │
│  ┌──────────────────────────────────┐   │    │                 │
│  │ Projector (folds events)         │   │    │                 │
│  └──────────────────────────────────┘   │    │                 │
└────────────────────────────────────────┘    └──────────────────┘
```

### After Sub 4.5

```
┌─────────────────────────────────────┐    ┌────────────────────────────────┐
│      wio-control-plane pod           │    │        wio-orchestrator pod    │
│       (HTTP + read-side only)        │    │       (control-flow only)      │
│                                      │    │                                │
│  ┌────────────────────────────────┐  │    │  ┌──────────────────────────┐ │
│  │ FastAPI / Socket.IO            │  │    │  │ DispatcherConsumer       │ │
│  └────────────────────────────────┘  │    │  │  (broker-sub)            │ │
│  ┌────────────────────────────────┐  │    │  └──────────────────────────┘ │
│  │ Publisher chain (slim)         │  │    │  ┌──────────────────────────┐ │
│  │  ┌─ EventLogAppend ─┐          │  │    │  │ TaskCreatorConsumer      │ │
│  │  ├─ Realtime        ┤          │  │    │  │  (broker-sub)            │ │
│  │  └─ Enriching       ┘          │  │    │  └──────────────────────────┘ │
│  │            │                   │──│────│─▶│ RunStartConsumer         │ │
│  │            ▼                   │  │    │  │  (broker-sub)            │ │
│  │         broker                 │◀─│────│──┤ ExecutorConsumer         │ │
│  └────────────────────────────────┘  │    │  │ OutboxDispatcher         │ │
│  ┌────────────────────────────────┐  │    │  │ OrchestrationWorker      │ │
│  │ Projector (folds events)       │  │    │  │  (ExecuteNodeAction)     │ │
│  └────────────────────────────────┘  │    │  └──────────────────────────┘ │
│  ┌────────────────────────────────┐  │    │                                │
│  │ EditNodeDataAction worker      │  │    │                                │
│  │ (PATCH /data only)             │  │    │                                │
│  └────────────────────────────────┘  │    │                                │
└─────────────────────────────────────┘    └────────────────────────────────┘
```

Both pods share the **broker (Event Mesh)**, the **event log
(HANA)**, and the **repos (HANA)**. Communication is purely via the
broker — no direct HTTP or RPC between control plane and orchestrator.

## 4. Sub-tasks

Five sub-PRs sized to merge independently. All but 4.5.5 land
behind a feature flag so production stays on the single-pod topology
until the soak window confirms parity.

| Sub | Deliverable | LoC | Behaviour change |
|---|---|---|---|
| **4.5.1** | Convert `DispatcherConsumer` from tee to broker-subscribed; delete `DispatcherTeeingPublisher` | ~150 | None when both pods are co-located; orderless redelivery now possible |
| **4.5.2** | Convert `TaskCreatorConsumer` to broker-subscribed; delete `TaskCreatorTeeingPublisher` | ~120 | Same as 4.5.1 |
| **4.5.3** | Convert `RunStartConsumer` to broker-subscribed; delete `RunStartTeeingPublisher` | ~120 | Same as 4.5.1 |
| **4.5.4** | Bootstrap split — `build_control_plane_container()` vs. `build_orchestrator_container()`; shared `_build_infrastructure()` | ~150 | New env var `APP_ROLE={control_plane,orchestrator,combined}` default `combined` (single-pod backward-compat) |
| **4.5.5** | Deployment artefacts — rename `Dockerfile.executor` → `Dockerfile.orchestrator`; update CF manifest + k8s deployment; HPA on `node.input` lag | ~80 | Production-visible: orchestrator pod appears |

**Total ~620 LoC.** The bulk is mechanical (rewriting tees as
subscribers, splitting bootstrap by role); little new logic.

## 5. Sub-PR sketches

### 5.1 Sub 4.5.1 — DispatcherConsumer broker migration

**Today** (`bootstrap.py:497`):

```python
event_publisher = DispatcherTeeingPublisher(
    inner=event_publisher,
    dispatcher=dispatcher_consumer,
)
dispatcher_consumer.set_publisher(event_publisher)
```

The teeing publisher intercepts `NodeOutputProduced` synchronously.

**After 4.5.1**:

```python
# bootstrap.py — inside build_orchestrator_container()
broker.subscribe_to_topic(
    EventTopic.NODE_OUTPUT_PRODUCED,
    lambda envelope: dispatcher_consumer.on_node_output_produced(
        _envelope_to_node_output_produced(envelope)
    ),
)
broker.subscribe_to_topic(
    EventTopic.LOOP_BODY_DISPATCHED,
    lambda envelope: dispatcher_consumer.on_loop_body_dispatched(...),
)
broker.subscribe_to_topic(
    EventTopic.LOOP_EXIT_DISPATCHED,
    lambda envelope: dispatcher_consumer.on_loop_exit_dispatched(...),
)
```

The `DispatcherTeeingPublisher` class is deleted.
`dispatcher_consumer._publisher` is bound to the orchestrator's own
publisher chain (which feeds the broker for re-emission of
`EdgeTraversed` / `NodeInput` / `WireSkipped` / `WireDeadLetter`).

**Mechanical concerns:**

- **Envelope deserialization.** Broker callbacks receive a `dict`
  (the JSON-decoded payload of the published envelope). The current
  in-process tee receives a typed `NodeOutputProduced` instance. A
  helper `_envelope_to_node_output_produced(dict) -> NodeOutputProduced`
  reverses the publisher's serialization. Same helper needed for the
  loop-body / loop-exit topics.
- **Ordering.** In-process tee runs synchronously; broker delivery is
  eventually consistent. Replay safety already handled by
  deterministic event ids — but a unit test should pin the case where
  two `NodeOutputProduced` events for the same run arrive
  out-of-order. The dispatcher's fan-out is per-event so order between
  events doesn't change the final state, only intermediate
  observability.
- **Test fixtures.** Tests today use a `_CapturingPublisher` and call
  `tee.publish(...)`. After migration the equivalent is calling
  `consumer.on_node_output_produced(typed_event)` directly. ~30
  test-file changes; mechanical.

### 5.2 Sub 4.5.2 — TaskCreatorConsumer broker migration

Identical shape to 4.5.1. Subscribes to `EventTopic.NODE_INPUT`.
The consumer's own re-publish (`TaskDispatched`) flows through the
orchestrator's broker publisher.

### 5.3 Sub 4.5.3 — RunStartConsumer broker migration

Subscribes to `EventTopic.RUN_STARTED` and `EventTopic.CHILD_RUN_STARTED`.

### 5.4 Sub 4.5.4 — Bootstrap split

New module structure:

```
bootstrap/
  __init__.py        # build_app_container() shim — preserves backward compat
  shared.py          # _build_infrastructure(): repos, broker, event_log,
                     # snapshot_repo, view_store, publisher_inner_chain
  control_plane.py   # build_control_plane_container(): HTTP, Socket.IO,
                     # projector tee, push-gateway, EditNodeData worker
  orchestrator.py    # build_orchestrator_container(): consumers, outbox,
                     # ExecuteNodeAction worker, executor consumer
```

**Role gating** via new `APP_ROLE` env var:

```python
class Settings(BaseSettings):
    APP_ROLE: Literal["control_plane", "orchestrator", "combined"] = (
        os.getenv("APP_ROLE", "combined").lower()
    )
```

`combined` (default) wires both — backward-compatible single-pod
deployment. Production migrates to two pods (`control_plane` +
`orchestrator`) after the soak.

**`main.py`** branches on `APP_ROLE`:

```python
if settings.APP_ROLE == "control_plane":
    container = build_control_plane_container(sio_server=sio_server)
elif settings.APP_ROLE == "orchestrator":
    container = build_orchestrator_container()
else:  # combined
    container = build_app_container(sio_server=sio_server)
```

The orchestrator binary doesn't host FastAPI; `app/executor_main.py`
(Sub 3.7) is the existing entry point and gets renamed to
`app/orchestrator_main.py` with its current responsibilities expanded
to include the three migrated consumers + `OrchestrationWorker` for
`ExecuteNodeAction`.

### 5.5 Sub 4.5.5 — Deployment artefacts

- Rename `deployment/Dockerfile.executor` → `deployment/Dockerfile.orchestrator`.
  CMD changes from `python -m app.executor_main` to `python -m
  app.orchestrator_main`.
- Rename `deployment/deployment-executor.yml` → `deployment/deployment-orchestrator.yml`.
  App name `wio-orchestrator`. Sets `APP_ROLE=orchestrator`. Sets
  `EXECUTOR_MODE=executor` (assumes Sub 4.4 has merged).
- Rename `deployment/k8s/executor-deployment.yaml` →
  `deployment/k8s/orchestrator-deployment.yaml`. HPA still on
  `kafka_consumergroup_lag` for `node.input`; add `run.started` and
  `node.output.produced` topics to the lag panel.
- Update `docs/operations/executor-deployment.md` →
  `docs/operations/orchestrator-deployment.md` to reflect the broader
  scope.

The control-plane Dockerfile + manifest stay; only `APP_ROLE` env
var differs.

## 6. The orchestration queue split

Today the `OrchestrationWorker` consumes five action types:

| Action | Producer | Consumer goes where |
|---|---|---|
| `StartRunAction` | `start_run` use case | Either pod — consumed once. Recommend orchestrator (control-flow). |
| `ContinueRunAction` | `complete_task` use case | Orchestrator — successor processing. |
| `FailTaskAction` | `fail_task` use case | Orchestrator — failure routing. |
| `ResumeTaskAction` / `ResumeRunAction` | `resume_pending_task` / `resume_run` use cases | Orchestrator — re-dispatch + audit. |
| `ExecuteNodeAction` | `_route_to_successors` (engine) + dispatcher fan-out | Orchestrator — node execution. |
| `LoopIterationAction` | `maybe_enqueue_loop_iteration` | Orchestrator. |
| `EditNodeDataAction` | PATCH `/data` HTTP handler | **Control plane** — the HTTP caller awaits the response on a per-instance correlation_id (M.3 sticky routing). |

**Decision.** Run two `OrchestrationWorker` instances — one per pod,
each filtered by action type:

```python
# control_plane.py
control_plane_worker = OrchestrationWorker(
    queue=orchestration_queue,
    engine=orchestration_engine,
    accepted_actions={EditNodeDataAction},
    edit_node_data_use_case=container.get("edit_node_data"),
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

The `accepted_actions` filter is a 4-line addition to
`OrchestrationWorker._dispatch`: skip actions not in the set.

**Alternative considered.** Two distinct queues (one per pod). Rejected
because Kafka consumer-group semantics already give per-action-type
routing for free if we partition by `action_type` — no schema change.

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Broker delivery delay between event publish and orchestrator consumption widens the window where HTTP `GET` returns stale state | Medium | Low — projector folds before HTTP read; consumer lag → projector lag, not data loss | Existing M.3 sticky routing + a metrics panel on per-topic consumer lag. Alert at lag > 1s p95. |
| Out-of-order delivery causes `NodeInput` to arrive before `RunStarted`'s task creation completes | Low | Medium — handler invokes with `task=None` | Consumer ordering already handled by deterministic ids + idempotent saves; add a "wait for predecessor task" check in `TaskCreatorConsumer` if needed. |
| Two pods both try to consume the same orchestration queue action and both succeed | Low | Medium — double-execute | Kafka consumer-group semantics + `accepted_actions` filter give exactly-once-per-action-type. Verified by an integration test in 4.5.4. |
| Test fixtures break because `tee.publish(...)` is no longer a thing | High | Low — mechanical | Provide a `_consumer_invoker` helper that routes a test event to the right consumer's callback. Migrate existing tests in the same PR as the consumer they cover. |
| Bootstrap split introduces a third surface (`combined`) that diverges over time | Medium | Low | Pin `combined` mode to call `build_control_plane_container()` then `build_orchestrator_container()` and merge — i.e. `combined = control_plane ∪ orchestrator` is enforced by composition, not duplication. |
| The orchestrator's broker subscription needs to dedupe with the executor's broker subscription on `node.input` | Low | High if missed — would invoke handlers twice | Both consumers use the same Kafka consumer group → only one consumes per partition. Verified by chaos test (Sub 3.6 already covers this for the executor; extend to dispatcher/task-creator/run-start). |
| HANA connection pool exhaustion if both pods open separate pools | Medium | Medium | Document expected pool size; existing `HANA_POOL_SIZE` setting works per-pod. |
| Cross-instance response routing for PATCH `/data` breaks if control plane and orchestrator both subscribe to the response topic | Low | High — duplicate response delivery | Per-instance response topics (Phase M.3) already key by `INSTANCE_ID`. Verify the orchestrator doesn't subscribe to the control plane's response topic. |

## 8. Test strategy

### 8.1 Unit tests (per sub-PR)

- 4.5.1 / 4.5.2 / 4.5.3 — verify the consumer behaves identically
  whether triggered by tee (current) or by direct callback (new).
  Existing test files reuse with one change: `tee.publish(t, e)` →
  `consumer.on_xxx(e)`.
- 4.5.4 — verify bootstrap branches correctly on `APP_ROLE`. Three
  tests: `combined` builds everything (existing test), `control_plane`
  builds HTTP-side only and orchestrator-side keys are absent,
  `orchestrator` builds consumers and HTTP-side keys are absent.

### 8.2 Integration tests

`tests/integration/test_orchestrator_split.py` (new):

1. **Two-process simulation** — wire both `build_control_plane_container`
   and `build_orchestrator_container` against the same in-memory
   broker stub. Submit a `POST /runs` to control plane; assert run
   completes via orchestrator-driven event chain.
2. **Action-type filter** — both pods consume the orchestration queue;
   assert each action type is processed exactly once.
3. **Lag tolerance** — inject 100ms broker delay; assert run still
   completes (no timeout in the chain).

### 8.3 Chaos tests (extend Sub 3.6)

Add cases for:
- Kill the orchestrator pod mid-`NodeInput` — control plane keeps
  running, second orchestrator instance picks up the redelivered
  event, idempotency preserves single Task entity.
- Kill the control plane mid-PATCH — orchestrator-driven flows
  unaffected.

## 9. Rollout gates

| Gate | Trigger | Target | Soak |
|---|---|---|---|
| **0 — prerequisites** | Sub 4.4 (`EXECUTOR_MODE=executor` default) merged | Phase 4 closed end-to-end on single pod | 1 week production |
| **1 — broker migration** | Sub 4.5.1–4.5.3 merged | Tees converted, `APP_ROLE=combined` default — no production flip | 2 weeks staging with consumer-lag panels green |
| **2 — bootstrap split** | Sub 4.5.4 merged | `APP_ROLE=combined` still default; `control_plane` + `orchestrator` available behind env flag | 1 week staging with `APP_ROLE=control_plane` + `APP_ROLE=orchestrator` running side-by-side |
| **3 — production flip** | Sub 4.5.5 merged | Production: deploy `wio-orchestrator` pod + flip control-plane pod to `APP_ROLE=control_plane` | 1 week prod soak; rollback path = revert to `combined` |
| **4 — single-pod retirement** | Gate 3 stable for 4 weeks | Delete `combined` mode, delete the shim in `bootstrap/__init__.py` | None — a follow-up cleanup PR |

## 10. Reference points in current code

- `DispatcherTeeingPublisher`: `app/layer4_frameworks/dispatcher/dispatcher_consumer.py:471`
- `TaskCreatorTeeingPublisher`: `app/layer4_frameworks/executor/task_creator_consumer.py:174`
- `RunStartTeeingPublisher`: `app/layer4_frameworks/executor/run_start_consumer.py:175`
- Bootstrap publisher chain: `bootstrap.py:307-507` (where the tees stack up)
- `OrchestrationWorker`: `app/layer4_frameworks/orchestration_queue/worker.py:55`
- `app/executor_main.py` (rename target): `workflow-control-plane-service/app/executor_main.py`
- Existing executor deployment: `deployment/Dockerfile.executor`,
  `deployment/deployment-executor.yml`,
  `deployment/k8s/executor-deployment.yaml`
- M.3 cross-instance response routing:
  `app/layer4_frameworks/orchestration_queue/response_topic_listener.py`

## 11. Open questions for stakeholder review

1. **Single-pod fallback retention.** Keep `APP_ROLE=combined` permanently
   (for dev convenience, integration tests) or retire after Gate 4? Recommend:
   keep. Cost is one composition-only branch in `bootstrap/__init__.py`.
2. **Projector co-location.** Continue running the projector on the
   control-plane pod for HTTP read-after-write, or move to its own pod
   with replicated read-only views? Recommend: keep on control plane
   for now; revisit if HTTP read scale demands it.
3. **`OrchestrationWorker` action-type filter** — implement as a
   parameter on the existing class (this doc's recommendation) or
   subclass into `ControlPlaneOrchestrationWorker` /
   `OrchestratorOrchestrationWorker`? Recommend: parameter; less code,
   easier to test, no new types.
4. **Should the broker subscriber use the kafka consumer-group offset
   commit semantics** (Sub 3.0 fixed for the executor) **for these
   three consumers too?** Recommend: yes — consumer-group ack on
   handler success only, identical to the executor's pattern. The
   shape is already there in `kafka_broker_client.py`.
5. **Outbox dispatcher placement.** Already on the executor pod
   (Sub 3.7). Stays on orchestrator. Confirm.

## 12. Estimated total cost

| Component | LoC | Risk |
|---|---|---|
| 4.5.1 DispatcherConsumer migration | ~150 | Medium — drift between in-process and broker semantics |
| 4.5.2 TaskCreatorConsumer migration | ~120 | Low — same shape as 4.5.1 |
| 4.5.3 RunStartConsumer migration | ~120 | Low — same shape |
| 4.5.4 Bootstrap split | ~150 | Medium — composition discipline |
| 4.5.5 Deployment artefacts | ~80 | Low — config / file moves |
| Test fixture migration | ~150 (across many test files) | Low — mechanical |
| Integration tests + chaos extensions | ~200 | Medium — broker stub fidelity |
| **Total** | **~970** | **Medium overall** |

For comparison: Phase 3 was ~2000 LoC. Sub 4.5 is roughly half that
size and lower risk because the consumer logic itself is unchanged —
only the trigger mechanism and the bootstrap topology shift.

## 13. Decision needed

Three things from the reviewer:

1. **Approve the split shape** (control plane = HTTP-only, orchestrator
   = control-flow, worker-executor untouched).
2. **Approve the orchestration queue split** — control plane keeps
   `EditNodeDataAction`, orchestrator owns the rest.
3. **Approve the rollout gates** — combined mode default, role split
   behind `APP_ROLE`, prod flip after 1-week prod soak.

If approved, kickoff target: 2026-W22 (post Sub 4.4 production flip).
