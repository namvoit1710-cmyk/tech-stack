"""``iter_progress`` keys are ints at runtime and strs after every reload.

Found while giving ``loop_state`` a home outside ``AIW_WORKFLOW_RUNS.CONTEXT``
(spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15). Writing the
row mapper meant deciding what the key type IS — and the answer turned out to be
"both, alternately, in production".

JSON has no integer keys. ``CONTEXT`` is ``json.dumps``/``json.loads`` with no
coercion at either end (``hana_run_repo._to_json`` / ``_from_json``), so the
runtime's ``iter_idx = int(scope.iter)`` writes ``{0: [...]}`` and gets
``{"0": [...]}`` back. Verified against prod-br10 / tenant-1: **11 of 11 runs
carrying ``iter_progress`` have string keys**, and every one still holds every
bucket it ever opened — e.g. a finished ``total=4`` loop stored
``{"0": ["body"], "1": ["body"], "2": ["body"], "3": ["body"]}``, which means
``_cleanup_iteration_state``'s ``pop(iteration_index)`` has never fired once in
production.

The damage is not the type drift, it is what the drift does on the NEXT save.
``json.dumps`` renders both ``"0"`` and ``0`` as ``"0"``, so a dict holding both
serialises to a duplicate JSON key — and ``json.loads`` keeps the last one. The
bucket that had the earlier body nodes loses to the bucket minted after the
reload.

Why it hid for so long: 10 of the 11 loops in production have a ONE-node body,
and one node per iteration survives the collapse intact. Only a multi-node body
notices, and then ``iteration_done`` returns False forever and the loop hangs
with no error and no failed task.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
    normalize_loop_state_keys,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer4_frameworks.persistence.hana.hana_run_repo import HanaRunRepository


# ────────────────────────────── helpers ──────────────────────────────


def _repo() -> HanaRunRepository:
    """The real serialize/deserialize pair. ``db`` is never touched by
    ``_context_to_dict`` / ``_row_to_entity``."""
    return HanaRunRepository(db=None)


def _save_and_reload(run: WorkflowRun) -> WorkflowRun:
    """One production round-trip: entity → CONTEXT NCLOB → entity."""
    repo = _repo()
    raw = repo._to_json(repo._context_to_dict(run.context))
    return repo._row_to_entity({
        "id": run.id, "status": run.status.value, "context": raw,
        "workflow_id": run.workflow_id, "workflow_version": run.workflow_version,
    })


def _two_node_body_workflow() -> tuple[Workflow, GraphTopology]:
    """loop → b1 → b2, plus an exit edge. The shape that hangs."""
    nodes = [
        WorkflowNode(id="loop", node_type=NodeType.LOOP),
        WorkflowNode(id="b1", node_type=NodeType.TASK, worker_type="w"),
        WorkflowNode(id="b2", node_type=NodeType.TASK, worker_type="w"),
        WorkflowNode(id="out", node_type=NodeType.TASK, worker_type="w"),
    ]
    loop_edges = [
        WorkflowEdge(source_node_id="loop", target_node_id="b1", condition="body"),
        WorkflowEdge(source_node_id="loop", target_node_id="out", condition="exit"),
    ]
    b1_edges = [WorkflowEdge(source_node_id="b1", target_node_id="b2")]
    topology = GraphTopology(
        edge_map={"loop": loop_edges, "b1": b1_edges, "b2": []},
        adjacency={}, predecessors={}, entry_nodes=[], leaf_nodes=[],
    )
    return Workflow(id="wf", nodes=nodes, edges=[]), topology


def _run(loop_state: dict, topology: GraphTopology | None = None) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf", workflow_version="1",
        status=RunStatus.RUNNING,
        context=RunContext(loop_state=loop_state,
                           topology=topology or GraphTopology()),
    )


def _record(run: WorkflowRun, iter_idx: int, node_id: str) -> None:
    """What ``LoopIterationDriver.record_body_progress`` does, verbatim
    (loop_iteration_driver.py:134-137) — an ``int`` key, always."""
    loop_state = run.context.loop_state["loop"]
    progress = loop_state.setdefault("iter_progress", {}).setdefault(iter_idx, [])
    if node_id not in progress:
        progress.append(node_id)


# ───────────────────── the hang, end to end ──────────────────────────


class TestAMultiNodeBodyAcrossReloads:
    """The production sequence. Each body node completes in its own callback,
    and ``orchestrate_continue`` re-fetches the run every time
    (``aget_by_id`` → fresh deserialize), so a reload sits BETWEEN them."""

    def test_both_body_nodes_survive_the_reload(self):
        workflow, topology = _two_node_body_workflow()
        run = _run({"loop": {"total": 1, "invocation_id": "inv",
                             "iter_progress": {0: []}}}, topology)

        _record(run, 0, "b1")
        run = _save_and_reload(run)          # b1's completion is persisted
        _record(run, 0, "b2")

        progress = run.context.loop_state["loop"]["iter_progress"]
        assert set(progress[0]) == {"b1", "b2"}, (
            "b1 was recorded before the reload and must still be there; losing "
            "it is what stalls the iteration"
        )

    def test_the_iteration_is_reported_done(self):
        """The consequence that matters. ``iteration_done`` is what decides
        whether ``LoopIterationAction`` is ever enqueued — False here means the
        loop sits in RUNNING until someone notices."""
        workflow, topology = _two_node_body_workflow()
        run = _run({"loop": {"total": 1, "invocation_id": "inv",
                             "iter_progress": {0: []}}}, topology)

        _record(run, 0, "b1")
        run = _save_and_reload(run)
        _record(run, 0, "b2")
        run.context.topology = topology      # topology is rebuilt on reload

        assert run.context.topology.iteration_done(run, "loop", 0, workflow) is True

    def test_a_second_save_does_not_destroy_the_first_node(self):
        """``orchestration_engine`` saves after EVERY loop-body completion
        (line ~1954, added precisely so multi-node bodies would not lose
        progress). Two reloads is the ordinary case, not the edge case."""
        run = _run({"loop": {"total": 1, "iter_progress": {0: []}}})

        _record(run, 0, "b1")
        run = _save_and_reload(run)
        _record(run, 0, "b2")
        run = _save_and_reload(run)
        _record(run, 0, "b3")

        assert set(run.context.loop_state["loop"]["iter_progress"][0]) == {
            "b1", "b2", "b3"
        }

    def test_a_single_node_body_was_always_fine(self):
        """Why this went unseen. 10 of the 11 loops in production look like
        this, and one node per iteration cannot lose a race with itself."""
        run = _run({"loop": {"total": 1, "iter_progress": {0: []}}})
        _record(run, 0, "body")
        run = _save_and_reload(run)
        _record(run, 0, "body")
        assert run.context.loop_state["loop"]["iter_progress"][0] == ["body"]


# ───────────────────────── the pruning no-op ─────────────────────────


class TestFinishedIterationsAreActuallyPruned:
    """``_cleanup_iteration_state`` pops with an ``int``
    (loop_iteration_driver.py:425). Against str keys that is a silent no-op —
    ``dict.pop`` with a default never raises, so nothing reported it."""

    def test_pop_removes_the_finished_bucket_after_a_reload(self):
        run = _run({"loop": {"total": 2, "iter_progress": {0: ["body"], 1: []}}})
        run = _save_and_reload(run)

        iter_progress = run.context.loop_state["loop"]["iter_progress"]
        iter_progress.pop(0, None)           # verbatim from _cleanup_iteration_state

        assert 0 not in iter_progress
        assert list(iter_progress) == [1]

    def test_the_production_shape_is_the_unpruned_one(self):
        """Straight from tenant-1: a finished ``total=4`` loop that kept all
        four buckets. If the pop had ever fired, at most one would remain."""
        stored = json.loads(
            '{"0": ["body"], "1": ["body"], "2": ["body"], "3": ["body"]}'
        )
        assert list(stored) == ["0", "1", "2", "3"]
        run = _run({"loop": {"iter_progress": stored}})
        assert list(run.context.loop_state["loop"]["iter_progress"]) == [0, 1, 2, 3]


# ──────────────────────── the normaliser itself ───────────────────────


class TestNormalizeLoopStateKeys:
    def test_string_indices_become_ints(self):
        state = {"loop": {"iter_progress": {"0": ["a"], "12": ["b"]}}}
        normalize_loop_state_keys(state)
        assert state["loop"]["iter_progress"] == {0: ["a"], 12: ["b"]}

    def test_iter_failed_nodes_too(self):
        """Same shape, same ``pop(iteration_index)``, same silent no-op — and
        it feeds ``outcome="failed"`` on LoopIterationCompleted."""
        state = {"loop": {"iter_failed_nodes": {"3": ["n"]}}}
        normalize_loop_state_keys(state)
        assert state["loop"]["iter_failed_nodes"] == {3: ["n"]}

    def test_a_damaged_bucket_is_repaired_by_union(self):
        """The shape a mid-flight run is already in. Letting one side win is
        exactly what ``json.loads`` does, and it picks the one that lost the
        earlier nodes."""
        state = {"loop": {"iter_progress": {"0": ["b1"], 0: ["b2"]}}}
        normalize_loop_state_keys(state)
        assert state["loop"]["iter_progress"] == {0: ["b1", "b2"]}

    def test_union_does_not_duplicate(self):
        state = {"loop": {"iter_progress": {"0": ["b1", "b2"], 0: ["b2", "b3"]}}}
        normalize_loop_state_keys(state)
        assert state["loop"]["iter_progress"] == {0: ["b1", "b2", "b3"]}

    def test_a_non_numeric_key_is_kept_not_dropped(self):
        """Nothing writes one today. Dropping data to enforce a type would be a
        worse bug than the one being fixed."""
        state = {"loop": {"iter_progress": {"0": ["a"], "unexpected": ["b"]}}}
        normalize_loop_state_keys(state)
        assert state["loop"]["iter_progress"] == {0: ["a"], "unexpected": ["b"]}

    def test_other_loop_state_keys_are_untouched(self):
        """``items`` and ``iteration_results`` are 99.99% of loop_state by
        volume. Walking into them would be pointless and expensive."""
        payload = {"__file_ref": True, "file_id": "f1"}
        state = {"loop": {"items": payload, "total": 4, "mode": "collection",
                          "completed_iterations": [0, 1]}}
        normalize_loop_state_keys(state)
        assert state["loop"]["items"] is payload
        assert state["loop"]["completed_iterations"] == [0, 1]

    def test_every_loop_in_the_run_is_normalised(self):
        state = {"outer": {"iter_progress": {"0": ["a"]}},
                 "inner": {"iter_progress": {"1": ["b"]}}}
        normalize_loop_state_keys(state)
        assert state["outer"]["iter_progress"] == {0: ["a"]}
        assert state["inner"]["iter_progress"] == {1: ["b"]}

    @pytest.mark.parametrize("junk", [None, [], "", 0, {"loop": None},
                                      {"loop": "x"}, {"loop": {"iter_progress": []}}])
    def test_junk_does_not_raise(self, junk):
        """This runs in ``RunContext.__post_init__`` — on the load path of every
        run in the system. It must never be the thing that takes a run down."""
        normalize_loop_state_keys(junk)


# ─────────────────── where the invariant is enforced ─────────────────


class TestTheInvariantBelongsToRunContext:
    def test_construction_normalises(self):
        """Placed in ``__post_init__`` rather than in the HANA repo so no
        future repository, cache or snapshot loader can reintroduce the drift."""
        ctx = RunContext(loop_state={"loop": {"iter_progress": {"0": ["a"]}}})
        assert ctx.loop_state["loop"]["iter_progress"] == {0: ["a"]}

    def test_the_hana_read_path_is_covered(self):
        run = _save_and_reload(_run({"loop": {"iter_progress": {0: ["a"]}}}))
        assert run.context.loop_state["loop"]["iter_progress"] == {0: ["a"]}

    def test_the_orm_read_path_is_covered(self):
        """The second deserialize site (``WorkflowRunORMEntity.to_domain``)."""
        from app.layer4_frameworks.orm.entities.workflow_run_entity import (
            WorkflowRunORMEntity,
        )
        entity = WorkflowRunORMEntity(
            id="r", workflow_id="wf", workflow_version="1", status="RUNNING",
            context=json.dumps({"loop_state": {"loop": {"iter_progress": {"0": ["a"]}}}}),
        )
        run = entity.to_domain()
        assert run.context.loop_state["loop"]["iter_progress"] == {0: ["a"]}

    def test_an_empty_context_costs_nothing(self):
        """6,105 of 6,116 runs have no loop_state at all — the guard has to
        short-circuit before walking anything."""
        assert RunContext().loop_state == {}
