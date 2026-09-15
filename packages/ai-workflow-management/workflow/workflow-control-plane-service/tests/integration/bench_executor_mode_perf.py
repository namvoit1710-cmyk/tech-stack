"""Throwaway measurement: EXECUTOR_MODE=in_process vs executor.

NOT a real test — a benchmark harness. Run with:

    python3 -m pytest tests/integration/bench_executor_mode_perf.py -s -q

Drives the SAME linear TRIGGER -> COMPUTE*K workflow to completion through
``build_app_container()`` twice, varying ONLY ``EXECUTOR_MODE`` (DISPATCHER_MODE
etc. held at their defaults). Reports, per mode:

  * events written to the event log, grouped by topic   <-- the portable metric
  * repo get/save counts (run / workflow / task)
  * wall-clock (memory mode: CPU + serialization ONLY; the in-process tee
    stands in for the broker, so this EXCLUDES the per-message network
    round-trip that dominates executor-mode cost on real Kafka/Event Mesh)

Each event the engine/executor publishes is ~one broker round-trip in prod, so
the per-node *message-count delta* is the honest predictor of production cost.
Wall-clock here is not.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)

_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
_TERMINAL_STR = {"completed", "failed", "cancelled"}
K = 8  # COMPUTE nodes in the chain (amortises fixed run-start overhead)


def _chain_workflow() -> Workflow:
    nodes = [WorkflowNode(id="t1", name="trigger", node_type=NodeType.TRIGGER, config={})]
    edges = []
    prev = "t1"
    for i in range(1, K + 1):
        nid = f"c{i}"
        nodes.append(WorkflowNode(
            id=nid, name=f"compute{i}", node_type=NodeType.COMPUTE,
            config={"expression": "21 * 2"},
        ))
        edges.append(WorkflowEdge(id=f"e{i}", source_node_id=prev, target_node_id=nid))
        prev = nid
    return Workflow(id="wf-perf", name="perf", version="1", nodes=nodes, edges=edges)


def _instrument(repo, names: list[str]) -> dict[str, int]:
    """Wrap ``repo``'s methods with call counters. Returns the live dict."""
    counts = {n: 0 for n in names}
    for name in names:
        orig = getattr(repo, name)

        def make(orig, name):
            def wrapper(*a, **k):
                counts[name] += 1
                return orig(*a, **k)
            return wrapper

        setattr(repo, name, make(orig, name))
    return counts


def _instrument_queue(queue) -> Counter:
    """Count orchestration-queue actions by type. In prod these are
    broker messages on ORCHESTRATION_TOPIC — NOT event-log entries — so
    they must be counted separately to compare total broker traffic."""
    counts: Counter = Counter()
    for name in ("enqueue", "enqueue_nowait"):
        orig = getattr(queue, name)

        def make(orig):
            def wrapper(action, *a, **k):
                counts[getattr(action, "action_type", type(action).__name__)] += 1
                return orig(action, *a, **k)
            return wrapper

        setattr(queue, name, make(orig))
    return counts


async def _drain_worker(worker, max_actions: int = 10000) -> None:
    """Non-blocking drain: process every queued action (and any it
    enqueues) until the queue empties. No timeout polling, so wall-clock
    reflects real work, not idle waits."""
    q = worker._queue._queue
    n = 0
    while not q.empty() and n < max_actions:
        action = q.get_nowait()
        await worker._dispatch(action)
        n += 1


def _terminal_from_store(run_repo, run_id: str) -> bool:
    """Read terminal status WITHOUT going through the instrumented
    ``get_by_id`` so driver bookkeeping doesn't pollute repo counts."""
    run = run_repo._store.get(run_id)
    return run is not None and run.status in _TERMINAL


async def _run_in_process(run_id: str) -> dict:
    import bootstrap
    orig = bootstrap.settings.EXECUTOR_MODE
    bootstrap.settings.EXECUTOR_MODE = "in_process"
    try:
        container = bootstrap.build_app_container()
    finally:
        bootstrap.settings.EXECUTOR_MODE = orig

    infra = container["_infra"]
    workflow_repo = infra.repos.workflow
    run_repo = infra.repos.run
    task_repo = infra.repos.task
    start_run = container["start_run"]
    worker = container["_orchestration_worker"]
    event_log = container["event_log_repo"]

    workflow_repo.save(_chain_workflow())

    repo_counts = {
        "run": _instrument(run_repo, ["get_by_id", "save"]),
        "workflow": _instrument(workflow_repo, ["get_by_id"]),
        "task": _instrument(task_repo, ["get_by_id", "save"]),
    }
    queue_counts = _instrument_queue(worker._queue)

    t0 = time.perf_counter()
    result = await start_run.execute(StartRunCommand(
        workflow_id="wf-perf", input_data={"seed": 1}, predefined_run_id=run_id,
    ))
    assert not result.error, f"in_process start_run error: {result.error}"
    for _ in range(80):
        await _drain_worker(worker)
        if _terminal_from_store(run_repo, run_id):
            break
    elapsed = time.perf_counter() - t0

    return _collect("in_process", run_id, event_log, run_repo, repo_counts,
                    queue_counts, elapsed)


async def _run_executor(run_id: str) -> dict:
    import bootstrap
    orig = bootstrap.settings.EXECUTOR_MODE
    bootstrap.settings.EXECUTOR_MODE = "executor"
    try:
        container = bootstrap.build_app_container()
    finally:
        bootstrap.settings.EXECUTOR_MODE = orig

    infra = container["_infra"]
    workflow_repo = infra.repos.workflow
    run_repo = infra.repos.run
    task_repo = infra.repos.task
    start_run = container["start_run"]
    worker = container["_orchestration_worker"]
    event_log = container["event_log_repo"]
    executor = container["_executor_consumer"]

    workflow_repo.save(_chain_workflow())
    executor.start()

    repo_counts = {
        "run": _instrument(run_repo, ["get_by_id", "save"]),
        "workflow": _instrument(workflow_repo, ["get_by_id"]),
        "task": _instrument(task_repo, ["get_by_id", "save"]),
    }
    queue_counts = _instrument_queue(worker._queue)

    t0 = time.perf_counter()
    result = await start_run.execute(StartRunCommand(
        workflow_id="wf-perf", input_data={"seed": 1}, predefined_run_id=run_id,
    ))
    assert not result.error, f"executor start_run error: {result.error}"

    seen: set[str] = set()
    for _ in range(400):
        await _drain_worker(worker)
        new = 0
        for env in event_log.list_by_run(run_id):
            if env.topic != EventTopic.NODE_INPUT or env.event_id in seen:
                continue
            seen.add(env.event_id)
            new += 1
            executor.notify(dict(env.payload or {}))
        processed = await executor.drain_once()
        if _terminal_from_store(run_repo, run_id):
            await _drain_worker(worker)
            break
        # Quiescent: no new NodeInput fed, executor idle, worker idle.
        if new == 0 and processed == 0 and worker._queue._queue.empty():
            break
    elapsed = time.perf_counter() - t0

    return _collect("executor", run_id, event_log, run_repo, repo_counts,
                    queue_counts, elapsed)


def _collect(mode, run_id, event_log, run_repo, repo_counts, queue_counts,
             elapsed) -> dict:
    envelopes = event_log.list_by_run(run_id)
    topics = Counter(e.topic for e in envelopes)
    run = run_repo._store.get(run_id)
    total_repo_reads = sum(
        cnts.get("get_by_id", 0) for cnts in repo_counts.values()
    )
    return {
        "mode": mode,
        "status": getattr(run, "status", None),
        "elapsed_ms": elapsed * 1000.0,
        "total_events": len(envelopes),
        "topics": dict(topics),
        "repo": {grp: dict(cnts) for grp, cnts in repo_counts.items()},
        "total_repo_reads": total_repo_reads,
        "queue": dict(queue_counts),
        "queue_total": sum(queue_counts.values()),
    }


@pytest.mark.asyncio
async def test_bench_executor_mode_perf():
    ip = await _run_in_process("run-perf-inprocess")
    ex = await _run_executor("run-perf-executor")

    assert ip["status"] == RunStatus.COMPLETED, f"in_process did not complete: {ip}"
    assert ex["status"] == RunStatus.COMPLETED, f"executor did not complete: {ex}"

    all_topics = sorted(set(ip["topics"]) | set(ex["topics"]))
    nodes = K + 1  # TRIGGER + K COMPUTE

    print("\n" + "=" * 72)
    print(f"  EXECUTOR_MODE perf comparison — TRIGGER -> COMPUTE*{K} chain "
          f"({nodes} nodes)")
    print("=" * 72)
    print(f"  {'metric':<34}{'in_process':>12}{'executor':>12}{'delta':>10}")
    print("  " + "-" * 68)

    def row(label, a, b):
        d = b - a
        ds = f"{d:+g}" if isinstance(d, float) else f"{d:+d}"
        print(f"  {label:<34}{a!s:>12}{b!s:>12}{ds:>10}")

    print("  DOMAIN EVENTS BY TOPIC (event-log writes ≈ broker msgs in prod)")
    for t in all_topics:
        row(f"    {t}", ip["topics"].get(t, 0), ex["topics"].get(t, 0))
    print("  " + "-" * 68)
    row("    TOTAL domain events", ip["total_events"], ex["total_events"])
    print("  " + "-" * 68)
    print("  ORCHESTRATION-QUEUE ACTIONS (broker msgs on ORCH topic, NOT event log)")
    all_actions = sorted(set(ip["queue"]) | set(ex["queue"]))
    for a in all_actions:
        row(f"    {a}", ip["queue"].get(a, 0), ex["queue"].get(a, 0))
    print("  " + "-" * 68)
    row("    TOTAL queue actions", ip["queue_total"], ex["queue_total"])
    row("  TOTAL BROKER MSGS (events+queue)",
        ip["total_events"] + ip["queue_total"],
        ex["total_events"] + ex["queue_total"])
    print("  " + "-" * 68)
    print("  REPO OPS (each get_by_id ≈ one HANA round-trip in prod)")
    for grp in ("run", "workflow", "task"):
        for op, a in sorted(ip["repo"][grp].items()):
            b = ex["repo"][grp].get(op, 0)
            row(f"    {grp}.{op}", a, b)
    row("    TOTAL repo reads (get_by_id)",
        ip["total_repo_reads"], ex["total_repo_reads"])
    print("  " + "-" * 68)
    print("  WALL-CLOCK  (memory mode: CPU+serialization only — NOT prod latency)")
    row("    elapsed_ms", round(ip["elapsed_ms"], 1), round(ex["elapsed_ms"], 1))
    print("=" * 72)

    msg_ip = ip["total_events"] + ip["queue_total"]
    msg_ex = ex["total_events"] + ex["queue_total"]
    read_factor = (ex["total_repo_reads"] / ip["total_repo_reads"]
                   if ip["total_repo_reads"] else 0)
    print(f"  Broker traffic:  in_process {msg_ip}  vs  executor {msg_ex}  "
          f"({msg_ex - msg_ip:+d}, ~parity)")
    print(f"  Repo-read load:  executor does {read_factor:.2f}x the reads "
          f"(+{ex['total_repo_reads'] - ip['total_repo_reads']} per "
          f"{nodes}-node run = "
          f"{(ex['total_repo_reads'] - ip['total_repo_reads']) / nodes:+.1f}/node)")
    print("  Memory-mode CPU is ~equal; the production cost is the extra DB")
    print("  reads (stateless re-hydration) + one more process hop / node.")
    print("=" * 72 + "\n")
