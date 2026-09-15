# Run aggregate — the decide / evolve seam

**Status:** design proposal (not started)
**Author:** orchestration refactor track
**Date:** 2026-06-18
**Scope:** `app/layer2_application/services/orchestration_engine.py` (2721 lines) and the
domain `WorkflowRun` aggregate.

---

## 0. TL;DR

> *"Can we create a Run domain entity that stores everything, and then we don't have
> an orchestration_engine anymore?"*

**Half yes, half no — and the "no" half is the important one.**

- **Yes:** the run's *state* and the run's *pure decisions* belong on/near the `Run`
  aggregate. Most of the state is already there (`RunContext`). The decisions are not —
  they are smeared across the engine, interleaved with I/O.
- **No:** you do **not** delete the orchestrator. Something has to consume from queues,
  call workers, publish broker events, and do CAS persistence. A domain entity that did
  that would import `event_publisher`, `run_repo`, and `file_service_client` — which
  **inverts the dependency rule and breaks the clean architecture this refactor is
  enforcing.** The engine doesn't vanish; it shrinks to a thin **imperative shell** whose
  only job is I/O.

The pattern is **Decider** (a.k.a. functional core / imperative shell):

```
decide(state, trigger) -> events      # PURE  — "what should happen?"
evolve(state, event)   -> state       # PURE  — "fold the event back into state"
```

You are **already halfway there**:

| Half | Already exists in the code | Where |
| --- | --- | --- |
| `evolve` | `apply_event(state, envelope) -> ProjectedRunState` — a pure reducer | `app/layer2_application/services/projector/run_state_projector.py:131` |
| `decide` (routing) | `compute_fanout(...) -> FanoutDecision` — a pure function | `app/layer2_application/services/dispatcher/fanout.py:86` |
| state | `RunContext` already holds variables / completed / skipped / loop / visits / merge buffers | `app/layer1_domain/entities/workflow_run.py:22` |

What's missing is (a) the **completion decision** is still tangled inside I/O, (b) the
pure pieces live in the application layer instead of on the domain aggregate, and (c)
there is no single named **shell** — the engine mixes both roles in every method.

This doc maps the full seam so the extraction can be done **incrementally, suite as
oracle**, not as a big-bang rewrite.

---

## 1. Why now / what's actually wrong

`OrchestrationEngine.__init__` injects **~18 I/O collaborators**
(`orchestration_engine.py:24`): `handler_registry`, `task_factory`, four repos
(`task_repo`, `run_repo`, `workflow_repo`, `workflow_snapshot_repo`), `event_publisher`,
`callback_publisher`, `file_service_client`, `csv_converter`, `variable_resolver`,
`data_store_service`, `monitor`, `event_driven_loop_driver`, `view_store`,
`scope_builder`, plus the `executor_mode` flag.

Every decision method reaches through those collaborators **while** it decides. The
canonical specimen is `check_run_completion` (`orchestration_engine.py:723`). In ~180
lines it:

1. reads tasks (`self._task_repo.alist_by_run`),
2. **decides** the run outcome (quiescent? failed? all-pending? — pure logic over the
   task list and `run.context`),
3. and immediately **acts**: CAS-saves the run, releases the scope lock, drops the
   resolver cache, records a stage, emits a status event, publishes a callback, and
   notifies the parent run.

Steps 1 and 3 are I/O. Step 2 is a pure function trapped between them. You cannot unit
test "does a run with these tasks complete?" without mocking five collaborators and a
CAS-retry closure. That is the cost.

The same shape repeats in `process_successors` (`:1509`),
`process_and_dispatch_successors` (`:1587`), `orchestrate_execute_node` (`:2019`),
`orchestrate_loop_iteration` (`:2466`), and the `orchestrate_*` lifecycle entry points.

**Good-taste read:** the engine has two responsibilities welded together. Split them.
The pure half goes to the domain (`Run.decide_*`), the reducer half is the projector you
already have (`evolve`), and the I/O half stays in a shell that gets dumber.

---

## 2. Target architecture

```
                          ┌─────────────────────────────────────────────┐
   queue / broker  ─────▶ │  RunOrchestrator (the SHELL — was the engine)│
   trigger (command)      │                                             │
                          │  1. load Run aggregate + Workflow + tasks    │  ← I/O (repos)
                          │  2. decision = run.decide_X(facts)           │  ← PURE call
                          │  3. perform I/O the decision implies:        │
                          │       - publish events / callbacks           │  ← I/O (broker)
                          │       - dispatch tasks to worker/executor    │  ← I/O
                          │       - CAS-save run                         │  ← I/O (repo)
                          │  4. evolve: events fold back into run state  │  ← PURE (apply_event)
                          └─────────────────────────────────────────────┘
                                         │ calls
                                         ▼
        ┌──────────────────────── layer1_domain ────────────────────────┐
        │  WorkflowRun (aggregate) + RunContext  ........... STATE       │
        │  run.decide_completion(tasks)  -> CompletionDecision           │
        │  run.decide_successors(task, wf, topo) -> FanoutDecision       │  PURE
        │  run.decide_loop_iteration(...) -> LoopDecision                │  (no I/O,
        │  evolve(state, event) -> state   (today: apply_event)          │   no async)
        └────────────────────────────────────────────────────────────────┘
```

**Dependency rule (the whole point):** arrows point **inward**. The shell depends on the
domain; the domain depends on nothing. `decide_*` and `evolve` may import `Workflow`,
`Task`, `WorkflowRun`, `DomainEvent`, `RunStatus`, `FanoutDecision` — all layer1 domain
types — and **must not** import any repo, publisher, client, or `async`/broker concern.
That one rule is what makes "everything in Run" *clean* instead of a god object.

### 2.1 Where decide / evolve physically live

Today the two pure functions sit in `layer2_application`. End-state, they move toward the
domain:

- **`evolve`** = today's `apply_event` reducer. It already only imports an
  `EventLogEnvelope` + dataclasses. It can move to `layer1_domain` as the canonical
  Run folder with no behavioural change. (The `ProjectedRunState` vs `WorkflowRun`
  duality is addressed in §6 — that convergence is the deep end and is **deferred**.)
- **`decide_*`** = methods on `WorkflowRun` (the OO "rich aggregate" the question asks
  for) **or** free functions in `layer1_domain` taking `WorkflowRun`. Either is clean;
  methods read better for this codebase. `compute_fanout` stays a free function and the
  method wraps it.

---

## 3. The full seam map

Every responsibility the engine has today, classified. `decide` = pure, returns a
decision/events. `evolve` = pure, folds an event into state. `shell` = I/O, stays in the
orchestrator. "Already pure" flags code that is *already* a pure function and just needs
relocating/wrapping.

| Engine responsibility | Today (file:line) | decide | evolve | shell |
| --- | --- | :---: | :---: | :---: |
| Run completion / fail / pending decision | `check_run_completion` `:723` | ✅ extract → `Run.decide_completion` | | CAS-save, emit, callback, parent-notify, lock release |
| Successor routing decision | `compute_fanout` `:1557` (**already pure**) | ✅ wrap → `Run.decide_successors` | | `_successor_processor.process` (dispatch) |
| Pending/port-override pre-routing | `process_successors` `:1534-1556` | ✅ fold into `decide_successors` | | — |
| Entry-node dispatch choice | `handle_entry_nodes` `:1459` | ✅ "which nodes, inline vs async" is pure | | `_exec_uniform`, `task_repo.asave`, enqueue |
| Loop iteration decision | `orchestrate_loop_iteration` `:2466`, `loop_iteration_driver` | ✅ extract → `Run.decide_loop_iteration` | | enqueue iteration, save |
| State-update write-set → events | `apply_state_updates_as_files` `:2564` | ✅ "what to write" is pure | | file-service upload, publish `NodeOutputProduced` |
| Run-lifecycle status fold | `RunStatusChanged` branch | | ✅ `apply_event` `:167` | emit event |
| Node-set fold (completed/skipped) | `NodeCompleted/Failed/Cancelled` | | ✅ `apply_event` `:218-233` | — |
| Edge-traversed un-skip (SA-846) | `EdgeTraversed` | | ✅ `apply_event` `:234` | — |
| Variables fold | `NodeOutputProduced` | | ✅ `apply_event` `:257` | — |
| Child-run tracking fold | `ChildRunStarted` | | ✅ `apply_event` `:243` | — |
| Outbox lifecycle fold | `OutboxRequested/Resolved` | | ✅ `apply_event` `:295-326` | — |
| CAS persistence | `save_run_with_cas_retry` | | | ✅ shell |
| Broker publish (events/callbacks) | `publish_event`, `emit_*`, `publish_callback` | | | ✅ shell |
| Task dispatch to worker / executor | `_exec_uniform`, dispatcher | | | ✅ shell |
| Scope lock / resolver cache | `self._scope.*` | | | ✅ shell |
| Stage recording / monitor | `_stage_recorder`, `_monitor` | | | ✅ shell |
| Run-scope ContextVar (`locked_run`) | `:501` | | | ✅ shell |
| Cross-instance routing / partition keys | publisher infra | | | ✅ shell |
| Child-workflow start / parent-task fail | `fail_parent_tracking_task` `:2713` | partial (decision pure) | | ✅ shell (the start/fail I/O) |
| Executor-mode branch | `_is_executor_mode` `:245` | | | ✅ shell (dispatch routing) |

**Reading the table:** the `evolve` column is essentially **done** (it's `apply_event`).
The `decide` column is the work — and two of its rows (`compute_fanout`, the pending
pre-routing) are *already pure*, just not on the aggregate. The `shell` column is
everything that must **never** move into the domain.

---

## 4. Worked example — decomposing `check_run_completion`

This is the template every slice follows. Today (`orchestration_engine.py:723`,
condensed):

```python
async def check_run_completion(self, run, workflow):
    if run.status in {COMPLETED, CANCELLED}: return
    topology = TopologyBuilder.prepare(workflow); run.context.topology = topology
    all_tasks = await self._task_repo.alist_by_run(run.id)          # I/O
    latest = _latest_per_node(all_tasks)                            # pure
    relevant = _filter_skipped(latest, run.context, self._view_skipped(run))  # pure
    has_active = ...; failed = ...                                  # pure
    if failed:
        run.status = FAILED; run.error = ...                       # decide
        await save_run_with_cas_retry(...)                         # I/O
        self.emit_run_status(run, "failed", ...)                   # I/O
        self.publish_callback(run, "FAILED")                       # I/O
        await self.fail_parent_tracking_task(run)                  # I/O
        return
    if run.context.pending_revisits and await self._drain...:      # I/O
        return
    if not has_active:
        run.status = COMPLETED; run.output_data = ...              # decide
        await save_run_with_cas_retry(...); emit; callback; parent # I/O
        return
    if has_active and all_pending and run.status == RUNNING:
        run.status = PENDING                                       # decide
        await save_run_with_cas_retry(...); emit                   # I/O
```

### After — pure decision on the aggregate

```python
# layer1_domain/entities/workflow_run.py  (PURE — no async, no collaborators)

@dataclass(frozen=True)
class CompletionDecision:
    kind: Literal["none", "failed", "completed", "pending", "revisit"]
    error: str = ""
    failed_task_id: str = ""
    output_data: dict | None = None
    revisit_nodes: frozenset[str] = frozenset()

class WorkflowRun:
    def decide_completion(self, latest_tasks: list[Task]) -> CompletionDecision:
        """Pure: given this run's status/context and the latest task per node,
        what lifecycle transition (if any) should happen? No I/O."""
        if self.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            return CompletionDecision("none")
        relevant = self._relevant_tasks(latest_tasks)        # skip-filter, pure
        failed = self._first_failed(relevant)
        if failed:
            return CompletionDecision("failed", error=..., failed_task_id=failed.id)
        if self.context.pending_revisits and self._has_idle_revisit(relevant):
            return CompletionDecision("revisit", revisit_nodes=...)
        if not self._has_active(relevant):
            return CompletionDecision("completed", output_data=self._output())
        if self._all_pending(relevant) and self.status == RunStatus.RUNNING:
            return CompletionDecision("pending")
        return CompletionDecision("none")
```

### After — the shell just acts on the decision

```python
# RunOrchestrator (was OrchestrationEngine) — thin, I/O only

async def check_run_completion(self, run, workflow):
    latest = _latest_per_node(await self._task_repo.alist_by_run(run.id))   # I/O
    decision = run.decide_completion(latest)                                # PURE
    match decision.kind:
        case "none":
            return
        case "revisit":
            await self._drain_pending_revisits(run, workflow); return
        case "failed":
            await self._finalize(run, RunStatus.FAILED, error=decision.error,
                                 failed_task_id=decision.failed_task_id)
        case "completed":
            await self._finalize(run, RunStatus.COMPLETED,
                                 output_data=decision.output_data)
        case "pending":
            await self._finalize(run, RunStatus.PENDING)
```

`_finalize` is the **one** place that does CAS-save + stage-record + emit + callback +
parent-notify. The branching/judgement is now a pure function you can test with a list of
fake tasks and zero mocks. **Behaviour is identical** — the suite is the oracle.

---

## 5. The decide catalog (extraction work-list)

Ordered by how cleanly separable they are today (easiest/safest first):

1. **`Run.decide_completion`** — `check_run_completion`. Self-contained, the logic is
   already a single read-then-branch. *Highest value, lowest risk. This is slice 1.*
2. **`Run.decide_successors`** — wrap `compute_fanout` + fold in the pending/port-override
   pre-routing (`:1534-1556`). `compute_fanout` is already pure; this is mostly relocation.
3. **`Run.decide_entry`** — `handle_entry_nodes` choice of "inline (PENDING) vs async" is
   pure; the dispatch stays in the shell.
4. **`Run.decide_loop_iteration`** — extract the iteration-vs-done judgement from
   `orchestrate_loop_iteration` / `loop_iteration_driver` (the SA-846
   `is_iteration_done` routing-aware logic).
5. **`Run.plan_state_updates`** — the "what write-set + which events" half of
   `apply_state_updates_as_files`; the file upload + publish stay in the shell.

Each is one slice: extract pure method → shell calls it → run touched tests → green →
next. No slice changes behaviour; each is independently revertible.

---

## 6. The deep end (explicitly deferred): one state, one evolve

Today there are **two** representations of run state:

- the **aggregate** `WorkflowRun` + `RunContext` (`workflow_run.py`), and
- the **projection** `ProjectedRunState` (`run_state_projector.py:64`), rebuilt by
  `apply_event`.

A textbook Decider has **one** state type that `evolve` folds events into. Converging
these two is the real "store everything in Run" end-state — `evolve` would fold directly
into a `WorkflowRun`, and the projector view becomes a cache of the aggregate rather than
a parallel shape.

**This is deferred and is NOT part of the first slices.** It touches the projector view
store, the cached view store, snapshotting (`SNAPSHOT_MODE`), and every read-side
consumer. It is a multi-week effort with real cross-instance-consistency risk. The decide
extraction (§5) delivers ~80% of the value (testability + logic no longer scattered)
without it. Do §5 first; revisit convergence only if the duality actually causes a bug.

---

## 7. Non-goals / what stays in the shell forever

These are I/O or framework concerns. They are **not** candidates for the domain, ever:

- Queue/broker consume + publish, partition keys, cross-instance response topics.
- CAS persistence (`save_run_with_cas_retry`) and all repository access.
- File-service upload/download, CSV conversion.
- Scope lock + resolver cache (`self._scope`), `locked_run` ContextVar.
- Worker/executor dispatch and the `EXECUTOR_MODE` routing branch.
- Stage recording, monitoring, callbacks.

A `Run` method that needs any of these is a sign the seam was cut in the wrong place —
push the I/O back to the shell and pass the result *in* as a plain value.

---

## 8. Migration plan

**Philosophy:** identical to the alias/shadow clean-up that preceded this — staged,
suite-as-oracle, every slice independently green and revertible. No big-bang.

| Slice | Change | Verify |
| --- | --- | --- |
| 1 | `Run.decide_completion` + shell `_finalize`; rewire `check_run_completion` | completion/quiescence unit tests + a run e2e |
| 2 | `Run.decide_successors` wrapping `compute_fanout` + pending pre-routing | routing/fanout unit tests, SA-846 revisit tests |
| 3 | `Run.decide_entry` | entry-node + parallel-input tests |
| 4 | `Run.decide_loop_iteration` | event-driven loop tests |
| 5 | `Run.plan_state_updates` | state-update + output-contract tests |
| (later) | move `apply_event` → domain `evolve` (no behaviour change) | projector rebuild test |
| (deferred) | converge `ProjectedRunState` ↔ `WorkflowRun` | full read-side suite |

**Test oracle:** system `pytest` (`/usr/local/bin/pytest`) from the service dir; no local
`.venv`. `pytest tests/ --collect-only` catches import-time breakage. Container-boot
integration tests are ~150s each — run them **one file/test at a time**, never batched
(they time out cumulatively). See `feedback_git_on_mnt_c_hangs` (verify via pytest, not
git).

**Definition of done per slice:** the extracted method has **zero** collaborator
references (no `self._*` repo/publisher/client), the shell method shrank, and the touched
test set is green.

---

## 9. Honest caveats

- **The engine does not disappear.** The deliverable is "the brain is pure and testable
  and lives on `Run`", not "one fewer file". If the goal is fewer files, this is the wrong
  project.
- **Bigger than the alias/shadow slices.** That was deletion; this is restructuring across
  the domain↔application boundary. Budget accordingly. Slices 1–2 alone are the high-value
  core; 3–5 are mop-up; §6 is a separate initiative.
- **No behaviour change is the contract.** Every slice must be a pure refactor proven by
  the existing suite. If a slice can't be made green without changing a test's
  expectation, stop — the seam is wrong, not the test.
- **Reversible.** Each slice is its own commit on the branch; back any of them out
  independently.

---

## 10. Answer to the original question, precisely

> *Create a Run domain entity that stores everything, then no orchestration_engine?*

Store the **state** (already mostly done) and the **decisions** (the work) on `Run`. Keep
a thin **shell** for I/O — renamed from `OrchestrationEngine` to something honest like
`RunOrchestrator`, ~several hundred lines of plumbing instead of ~2700 of tangled
logic. That shell is the part that *can't* be a domain entity without breaking clean
architecture. So: **richer Run, much dumber engine — not no engine.**

---

## 11. Recommended next step (slice 1, concrete)

**Start with `Run.decide_completion`.** It is the highest-value, lowest-risk cut: the
logic in `check_run_completion` (`orchestration_engine.py:723`) is already a single
read-then-branch, it is self-contained, and it is the worst current offender for
"untestable without five mocks". Doing it first proves the whole pattern on the hardest-
to-test method.

**Exact change:**

1. Add to `app/layer1_domain/entities/workflow_run.py` (pure — no `async`, no `self._*`
   collaborators):
   - a frozen `CompletionDecision` dataclass
     (`kind ∈ {none, failed, completed, pending, revisit}` + `error`, `failed_task_id`,
     `output_data`, `revisit_nodes`), and
   - `WorkflowRun.decide_completion(self, latest_tasks: list[Task]) -> CompletionDecision`
     holding the skip-filter, first-failed, idle-revisit, quiescence, and all-pending
     judgement currently inlined at `:737-900`.
2. In the engine, add one private `_finalize(run, status, **outcome)` that is the **single**
   site performing CAS-save + `_scope.release_lock` + `_scope.drop_resolver_cache` +
   `_stage_recorder.record` + `emit_run_status` + `publish_callback` + parent-notify.
3. Rewrite `check_run_completion` to: fetch tasks (I/O) → `run.decide_completion(latest)`
   (pure) → `match decision.kind` → call `_finalize` / `_drain_pending_revisits` / return.
   See the worked code in §4.

**Invariants to preserve (do not regress):**

- FAILED→COMPLETED recovery (`run.error` cleared on the completed branch, `:851`).
- SA-846 Mode-A revisit drain runs **before** the quiescence decision (`:838`).
- "latest task per node" + skip-filter with the un-skip exception (`:762-769`).
- Parent-tracking-task notify on both the failed and completed terminal branches.

**Definition of done:** `decide_completion` has **zero** collaborator references; the
engine method shrank; behaviour is byte-identical (pure refactor).

**Verify (run one at a time — container boot ~150s, batching times out):**

```
/usr/local/bin/pytest tests/ --collect-only          # import-time breakage check
/usr/local/bin/pytest <completion/quiescence unit tests>
/usr/local/bin/pytest tests/integration/<one run e2e>::<one test>
```

If any existing test needs its **expectation** changed to pass, stop — the seam was cut
wrong, not the test. Back the slice out (its own commit) and re-cut.
