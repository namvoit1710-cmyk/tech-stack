# -*- coding: utf-8 -*-
"""``Task.execution_time_ms`` — the field that was assigned but never declared.

Commit 70ac77655 (2026-08-11, "wcp: the worker told us how long it took and we
threw it away") added this read to ``orchestration_engine`` line 853::

    execution_time_ms=task.execution_time_ms or 0,

``Task`` had no such field. It existed only because ``complete_task_use_case``
assigns it on a plain dataclass, and only ``if req.execution_time_ms:`` — so a
task that never went through that path, with that condition true, raised
``AttributeError``. ``or 0`` cannot save a read that fails before the ``or``.

The one caller of ``_mark_task_completed`` is ``loop_iteration_driver`` line 361,
driving the LOOP node's own tracking task terminal. The engine creates that task;
it never goes near ``complete_task_use_case``, so for a loop the attribute could
never exist and the raise was certain, not occasional.

What made it serious is what sits AFTER the call, outside any ``except`` (the
``try`` at line 290 closes with ``finally`` at 321)::

    361  await self._engine._mark_task_completed(loop_task, run)   <- raised
    367  await self._run_repo.asave(run)                           <- skipped
    368  await self._engine.drain_events()                         <- skipped
    369  await self._engine.check_run_completion(run, workflow)    <- skipped

and the task is already saved COMPLETED at line 834, before the raise. So the
predicted signature is a loop whose tracking task reads COMPLETED while the run
never quiesces.
"""
import dataclasses

from app.layer1_domain.entities.task import Task, TaskResult


class TestTheFieldIsDeclared:
    def test_a_fresh_task_has_it(self):
        """A task the ENGINE built — never near a worker — must still answer."""
        assert Task.create(run_id="r", node_id="n").execution_time_ms == 0

    def test_it_is_a_real_dataclass_field_not_an_assigned_attribute(self):
        """An assigned attribute exists on ONE instance. A declared field exists
        on every instance, including one rebuilt by the repository."""
        names = {f.name for f in dataclasses.fields(Task)}
        assert "execution_time_ms" in names

    def test_it_survives_dataclasses_replace(self):
        """``with_pushed_scope`` uses ``dataclasses.replace``; an undeclared
        attribute is dropped by it silently."""
        t = dataclasses.replace(Task.create(run_id="r", node_id="n"),
                                execution_time_ms=1250)
        assert dataclasses.replace(t, node_id="n2").execution_time_ms == 1250

    def test_task_and_taskresult_both_carry_it(self):
        """The field was on ``TaskResult`` only, which is why the read looked
        correct when written."""
        assert TaskResult().execution_time_ms == 0
        assert Task.create(run_id="r", node_id="n").execution_time_ms == 0


class TestTheReadThatRaised:
    def test_the_exact_expression_from_line_853(self):
        """``task.execution_time_ms or 0`` — the ``or 0`` never ran, because
        the attribute lookup raised first."""
        task = Task.create(run_id="r", node_id="loop-1")
        assert (task.execution_time_ms or 0) == 0

    def test_a_worker_reported_duration_is_kept(self):
        """``complete_task_use_case`` assigns this; declaring the field must not
        change that behaviour, only make it survive a task that never got one."""
        task = Task.create(run_id="r", node_id="n")
        task.execution_time_ms = 1250
        assert (task.execution_time_ms or 0) == 1250


class TestWhatIsStillNotFixed:
    """Recorded so the gap is not mistaken for closed by a green test run."""

    def test_the_field_is_not_persisted(self):
        """``hana_task_repo`` writes an explicit column list and this is not in
        it, so a reloaded task reports 0 however long the worker took. Closing
        that needs a column and a migration; it is not this change. The one
        reader today is the LOOP tracking task, where 0 is correct.

        This test exists to FAIL when someone adds the column, so the docstring
        above cannot quietly go stale.
        """
        import inspect

        from app.layer4_frameworks.persistence.hana import hana_task_repo

        src = inspect.getsource(hana_task_repo)
        assert "EXECUTION_TIME_MS" not in src.upper(), (
            "a column was added — persistence now works, so update "
            "Task.execution_time_ms's docstring and delete this test"
        )
