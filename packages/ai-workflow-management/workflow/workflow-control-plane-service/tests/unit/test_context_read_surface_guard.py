"""Freeze the set of places that read run.context off the AGGREGATE.

``AIW_WORKFLOW_RUNS.CONTEXT`` is dropped. Every field it carried now reads back
EMPTY, and the failure is silent — no exception, no failed task, just a wrong
answer. Three separate incidents have already come out of this one cause:

* ``topology``   — a run completed having executed NOTHING (PR #1857)
* ``child_runs`` — sub-workflow drill-in blank, cancel stopped cascading (#1868)
* the port mirrors — resumed PENDING nodes fan out over every edge

PR #1857's own commit message records why a per-site fix is not enough: the
audit that cleared the drop *"recorded topology as safe — rebuilt from
AIW_WORKFLOWS, which is true of the projector VIEW and false of the aggregate."*
The same conflation produced the other two. This test is the part that does not
depend on anyone remembering.

It does not judge whether a read is correct — it cannot. It asserts the SET of
reads has not changed, so adding one forces a reviewer to write down why it is
safe. Deleting one forces the entry to go, so the list cannot rot into cover.

Guard both spellings: ``run.context.<field>`` and ``getattr(x, "<field>")`` —
``get_run_artifacts`` and ``rerun_run`` use the second, and an earlier pass of
this audit missed them by only looking for the first.
"""

from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# Fields DROP CONTEXT destroys. ``loop_state`` / ``node_visits`` /
# ``pending_revisits`` are deliberately absent: the repo rehydrates them from
# AIW_RUN_LOOP_STATE / AIW_RUN_NODE_STATE (LOOP_STATE_READ_MODE=table,
# NODE_STATE_READ_MODE=table). ``topology`` is absent: the engine rebuilds it in
# ``_ensure_topology``. Add a field here the moment its rehydration is removed.
GUARDED_FIELDS = frozenset({
    "variables", "completed_nodes", "skipped_nodes", "child_runs",
    "last_output_ports", "last_taken_edge_ports", "current_node",
})

_HANDLER = "handler — _exec_uniform hydrates variables from the view before invoking"
_ENGINE_VIEW = "engine — reads through the _view_* helpers, which union the projector"
_IN_FRAME = "in-frame — reads what it just wrote on the same object, never a reload"
_TEST_RESET = "test/reset path — mutates an in-memory run it owns for that call"
_VIEW_SRC = "view-sourced — getattr on a projector view/snapshot, not the aggregate"
_CHOKEPOINT = "the chokepoint itself"

# (relative path) -> {field: why this read is safe, or which spec item tracks it}
ALLOWED: dict[str, dict[str, str]] = {
    "layer1_domain/entities/run_context_snapshot.py": {
        "last_output_ports": _VIEW_SRC, "last_taken_edge_ports": _VIEW_SRC,
    },
    "layer1_domain/entities/workflow_run.py": {
        "skipped_nodes": "decide_completion takes view_skipped as an argument; "
                         "agg_skipped is only the un-skip signal, paired with "
                         "node_visits (rehydrated from AIW_RUN_NODE_STATE)",
        "last_output_ports": "fed by OrchestrationEngine._ensure_routing_state, "
                             "which refills the reloaded aggregate from the projector",
        "last_taken_edge_ports": "fed by _ensure_routing_state, same call site",
    },
    "layer1_domain/value_objects/resolution_scope.py": {"variables": _HANDLER},
    "layer2_application/credentials/run_credential_provider.py": {"variables": _IN_FRAME},
    "layer2_application/credentials/run_credential_seeder.py": {"variables": _IN_FRAME},
    "layer2_application/features/complete_task/use_cases/complete_task_use_case.py": {
        "variables": _IN_FRAME},
    "layer2_application/features/get_node_data/use_cases/get_node_data_use_case.py": {
        "variables": _VIEW_SRC},
    "layer2_application/features/get_node_execution/use_cases/get_node_execution_use_case.py": {
        "variables": "falls back to view.variables at :127"},
    "layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py": {
        "variables": "merged with view.variables at :640"},
    "layer2_application/features/get_variable_suggestions/use_cases/scope_providers.py": {
        "variables": "merged with view.variables at :639/:736"},
    "layer2_application/features/rerun_run/use_cases/rerun_run_use_case.py": {
        "completed_nodes": _TEST_RESET, "skipped_nodes": _TEST_RESET,
        "variables": _TEST_RESET,
        "last_output_ports": "BROKEN — spec A6", "last_taken_edge_ports": "BROKEN — spec A6"},
    "layer2_application/features/rerun_task/use_cases/rerun_task_use_case.py": {
        "completed_nodes": _TEST_RESET, "skipped_nodes": _TEST_RESET,
        "variables": _TEST_RESET,
        "last_output_ports": "BROKEN — spec A6", "last_taken_edge_ports": "BROKEN — spec A6"},
    "layer2_application/features/reset_test_run/use_cases/reset_test_run_use_case.py": {
        f: _TEST_RESET for f in
        ("child_runs", "completed_nodes", "skipped_nodes",
         "last_output_ports", "last_taken_edge_ports")},
    "layer2_application/features/resolve_variable_paths/use_cases/resolve_variable_paths_use_case.py": {
        "variables": _VIEW_SRC},
    "layer2_application/features/resume_pending_task/use_cases/resume_pending_task_use_case.py": {
        "variables": "reads view.variables at :562/:600",
        "completed_nodes": _IN_FRAME, "skipped_nodes": _IN_FRAME},
    "layer2_application/features/start_run/use_cases/start_run_use_case.py": {
        "variables": _IN_FRAME},
    "layer2_application/features/test_full_workflow/use_cases/test_full_workflow_use_case.py": {
        **{f: _TEST_RESET for f in
           ("child_runs", "completed_nodes", "skipped_nodes",
            "last_output_ports", "last_taken_edge_ports")},
        # Read to PUBLISH, a few statements after this use case assigned it, so
        # the value reaches the projector before ``orchestrate_start`` reloads
        # the run by id. Same in-frame read ``start_run`` does, and for the same
        # reason: without it, what the use case just wrote never leaves the
        # process, which is precisely the bug this entry accompanies.
        "variables": _IN_FRAME},
    "layer2_application/features/test_node/use_cases/test_node_use_case.py": {
        f: _TEST_RESET for f in
        ("child_runs", "completed_nodes", "skipped_nodes", "variables",
         "last_output_ports", "last_taken_edge_ports")},
    "layer2_application/node_handlers/merge_handler.py": {"variables": _HANDLER},
    "layer2_application/node_handlers/pending_base.py": {"variables": _HANDLER},
    "layer2_application/node_handlers/schema_providers.py": {"variables": _HANDLER},
    "layer2_application/orchestration/child_workflow_coordinator.py": {
        "child_runs": _IN_FRAME, "completed_nodes": _IN_FRAME,
        "skipped_nodes": _IN_FRAME, "variables": _IN_FRAME},
    "layer2_application/orchestration/loop_driver/event_driven_loop_driver.py": {
        "completed_nodes": _ENGINE_VIEW, "variables": _ENGINE_VIEW},
    "layer2_application/orchestration/loop_iteration_driver.py": {
        "completed_nodes": _ENGINE_VIEW, "skipped_nodes": _ENGINE_VIEW,
        "variables": _ENGINE_VIEW},
    "layer2_application/orchestration/node_execution_runner.py": {
        "completed_nodes": _IN_FRAME, "skipped_nodes": _IN_FRAME},
    "layer2_application/orchestration/orchestration_engine.py": {
        "completed_nodes": _ENGINE_VIEW, "skipped_nodes": _ENGINE_VIEW,
        "variables": _ENGINE_VIEW,
        "last_output_ports": "write-side mirror + _ensure_routing_state (spec A)",
        "last_taken_edge_ports": "write-side mirror + _ensure_routing_state (spec A)"},
    "layer2_application/orchestration/successor_processor.py": {
        "completed_nodes": "unions view.completed_nodes at :206-209",
        "skipped_nodes": "deliberate aggregate read at :215 (un-skip must be "
                         "synchronous) — re-examine, the aggregate no longer "
                         "survives a reload",
        "variables": _ENGINE_VIEW},
    "layer2_application/read/run_state.py": {
        "child_runs": _CHOKEPOINT, "skipped_nodes": _CHOKEPOINT,
        "variables": _CHOKEPOINT, "completed_nodes": _CHOKEPOINT},
    "layer2_application/read/node_data_projection.py": {
        "completed_nodes": _VIEW_SRC, "skipped_nodes": _VIEW_SRC},
    "layer2_application/run_file_retention.py": {
        "variables": "retention_variables() falls back to the view store"},
    "layer4_frameworks/persistence/hana/hana_run_node_state_repo.py": {
        "completed_nodes": "docstring reference only", "skipped_nodes": "docstring reference only",
        "last_output_ports": "docstring reference only"},
}

# Every node handler reaches variables through the hydrated aggregate.
for _h in (
    "approval_flow", "compute", "condition", "data_edit", "file_upload",
    "human_action", "input", "output", "parallel", "prompt", "set_variable",
    "switch", "task", "workflow",
):
    ALLOWED.setdefault(f"layer2_application/node_handlers/{_h}_handler.py", {})["variables"] = _HANDLER


def _actual_reads() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        hits: set[str] = set()
        for node in ast.walk(tree):
            # run.context.<field>
            if (isinstance(node, ast.Attribute) and node.attr in GUARDED_FIELDS
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "context"
                    and isinstance(node.ctx, ast.Load)):
                hits.add(node.attr)
            # getattr(x, "<field>", ...)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr" and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in GUARDED_FIELDS):
                hits.add(node.args[1].value)
        if hits:
            out[path.relative_to(APP).as_posix()] = hits
    return out


class TestContextReadSurfaceIsFrozen:
    def test_no_unreviewed_aggregate_reads(self):
        actual = _actual_reads()
        new: list[str] = []
        for mod, fields in sorted(actual.items()):
            for f in sorted(fields - set(ALLOWED.get(mod, {}))):
                new.append(f"  {mod}  ->  .context.{f}")
        assert not new, (
            "New read(s) of a run.context field that DROP CONTEXT left EMPTY:\n"
            + "\n".join(new)
            + "\n\nThe aggregate is not a source any more — it reads {} on every"
              "\nrun created since 2026-08-08, silently. Read the projector"
              "\ninstead (see app/layer2_application/read/run_state.py for the"
              "\nshape), or add an entry to ALLOWED here with the reason it is"
              "\nsafe. Do not add it without the reason."
        )

    def test_allowlist_has_no_stale_entries(self):
        """A list that keeps entries for reads that no longer exist stops being
        a record of the real surface and starts being cover."""
        actual = _actual_reads()
        stale: list[str] = []
        for mod, fields in sorted(ALLOWED.items()):
            got = actual.get(mod, set())
            for f in sorted(set(fields) - got):
                stale.append(f"  {mod}  ->  .context.{f}")
        assert not stale, (
            "ALLOWED lists read(s) that no longer exist — delete them:\n"
            + "\n".join(stale)
        )

    def test_every_context_field_declares_how_it_survives_a_reload(self):
        """``RunContext`` gaining a field without saying how it survives a
        reload must not pass unnoticed. Three honest outcomes, no fourth."""
        from app.layer1_domain.entities.workflow_run import RunContext

        # Rebuilt on load by a named mechanism.
        REHYDRATED = {
            "loop_state": "hana_run_repo._resolve_loop_state (LOOP_STATE_READ_MODE=table)",
            "node_visits": "hana_run_repo._resolve_node_state (NODE_STATE_READ_MODE=table)",
            "pending_revisits": "hana_run_repo._resolve_node_state",
            "topology": "OrchestrationEngine._ensure_topology",
        }
        # Never persisted and not projector-backed — absent from
        # ``_context_to_dict``, ``RunContextSnapshot`` and ``run_projection``
        # alike, which pre-dates DROP CONTEXT rather than being caused by it.
        # Correct ONLY while every consumer lives inside one call frame.
        EPHEMERAL = {
            "triggered_nodes": "in-memory only; dies on reload, always has",
            "merge_quorum_buffers": "in-memory only; a straggler arriving after a "
                                    "reload finds an empty buffer and can re-fire "
                                    "the downstream subtree (known, pre-existing)",
        }
        declared = GUARDED_FIELDS | set(REHYDRATED) | set(EPHEMERAL)
        actual = {f.name for f in RunContext.__dataclass_fields__.values()}
        missing = actual - declared
        assert not missing, (
            f"RunContext field(s) {sorted(missing)} declare nothing about "
            "surviving a reload. Pick one and say so here: guarded (read the "
            "projector), REHYDRATED (name the mechanism), or EPHEMERAL (state "
            "that it dies on reload and why that is acceptable)."
        )
        stale = declared - actual - GUARDED_FIELDS
        assert not stale, f"declared field(s) {sorted(stale)} no longer exist on RunContext"
