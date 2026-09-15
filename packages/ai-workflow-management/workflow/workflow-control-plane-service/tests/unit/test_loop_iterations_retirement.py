"""Loop walker retirement.

The legacy imperative loop walker (``_run_loop_iterations``) and the
recursive ``dispatch_or_handle_task`` DAG walker are fully removed. Loop
iteration now runs entirely through ``_exec_uniform``'s ``loop_body``
dispatch and the event-driven loop driver (``maybe_enqueue_loop_iteration``
→ ``LoopIterationAction`` → ``orchestrate_loop_iteration``).

The body-task save + ``ExecuteNodeAction`` enqueue behaviour the old shim
provided is covered by ``_exec_uniform``'s ``loop_body`` branch and the
event-driven loop tests.

What remains to verify here: bootstrap always wires the loop driver — the
``EVENT_DRIVEN_LOOP=false`` opt-out is a no-op.
"""

from __future__ import annotations


class TestBootstrapAlwaysWiresLoopDriver:
    def test_default_settings_wires_driver(self):
        from bootstrap import build_app_container
        c = build_app_container()
        # Engine isn't on the container dict directly — pull it via
        # the orchestration worker which holds a reference.
        engine = c["_orchestration_worker"]._engine
        assert engine._event_driven_loop_driver is not None

    def test_event_driven_loop_false_is_no_op(self, monkeypatch):
        """The retired flag is silently ignored — the driver wires
        regardless of ``EVENT_DRIVEN_LOOP=false``."""
        monkeypatch.setenv("EVENT_DRIVEN_LOOP", "false")
        from app.layer4_frameworks.config import app_config
        # Force-reload settings to pick up the env override for this test.
        app_config.settings.EVENT_DRIVEN_LOOP = False

        from bootstrap import build_app_container
        c = build_app_container()
        engine = c["_orchestration_worker"]._engine
        assert engine._event_driven_loop_driver is not None
