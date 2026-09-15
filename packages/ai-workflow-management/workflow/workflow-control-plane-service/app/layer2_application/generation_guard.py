"""Shared generation-guard check for task callback handlers.

See ``docs/design/run-generation-guard.md``.

``CompleteTaskUseCase`` and ``FailTaskUseCase`` call ``should_reject_callback``
after loading the task and run; when it returns ``True`` they short-circuit
with a ``TASK_SUPERSEDED`` result (HTTP 409) and emit nothing — the callback
belongs to a superseded execution generation (e.g. the test run was
re-executed between dispatch and callback arrival).

Tri-state driven by ``settings.GENERATION_GUARD_MODE``:

* ``off``    — no-op.
* ``shadow`` — log and increment a ``superseded_generation`` metric on
               mismatch, but do not reject. Used during soak to measure
               the rate of would-be rejections.
* ``on``     — enforce rejection on mismatch.

Strictly ``<``: a task with ``run_generation > run.generation`` is a bug
(task stamped before the run save succeeded) — logged loudly, still
rejected when mode is ``on``.
"""
from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.interfaces.app_config_interface import IAppConfig


_MODE_OFF = "off"
_MODE_SHADOW = "shadow"
_MODE_ON = "on"


def _resolve_mode(app_config: IAppConfig | None) -> str:
    raw = getattr(app_config, "GENERATION_GUARD_MODE", "on") if app_config is not None else "on"
    mode = (raw or "on").lower()
    if mode not in (_MODE_OFF, _MODE_SHADOW, _MODE_ON):
        return _MODE_ON
    return mode


def should_reject_callback(
    task: Task,
    run: WorkflowRun,
    logger: Any,
    handler: str,
    monitor: Any = None,
    *,
    app_config: IAppConfig | None = None,
) -> bool:
    """Return True when the callback should be rejected by the caller.

    ``handler`` is a short tag (``"complete"`` / ``"fail"``) included in
    log and metric tags so operators can slice by handler in the soak
    dashboard.
    """
    task_gen = getattr(task, "run_generation", 0) or 0
    run_gen = getattr(run, "generation", 0) or 0

    if task_gen == run_gen:
        return False

    mode = _resolve_mode(app_config)

    # ``task_gen > run_gen`` is anomalous — log loudly regardless of mode.
    future_stamp = task_gen > run_gen

    if logger is not None:
        log_fn = logger.error if future_stamp else logger.warning
        log_fn(
            "Task callback generation mismatch",
            handler=handler,
            run_id=run.id,
            task_id=task.id,
            task_generation=task_gen,
            run_generation=run_gen,
            mode=mode,
            future_stamp=future_stamp,
        )

    if monitor is not None and hasattr(monitor, "increment"):
        try:
            monitor.increment(
                "workflow.task.callback.superseded_generation",
                tags={"handler": handler, "mode": mode},
            )
        except Exception:
            pass

    if mode == _MODE_OFF:
        return False
    if mode == _MODE_SHADOW:
        return False
    return True
