"""G-guard unit tests — ``generation_guard.should_reject_callback``.

See ``docs/design/run-generation-guard.md``.

Covers the three modes (off | shadow | on), the strict ``<`` comparison,
and the anomalous ``task_generation > run_generation`` log path.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.generation_guard import should_reject_callback
from tests.conftest import DummyLogger, DummyMonitor


def _reject(task, run, logger, handler, monitor=None):
    """Invoke the guard with the live settings singleton as app_config so the
    ``_set_mode`` patches below drive the decision."""
    from app.layer4_frameworks.config import app_config
    return should_reject_callback(
        task, run, logger, handler, monitor, app_config=app_config.settings
    )


def _task(generation: int) -> Task:
    return Task(id="t1", run_id="r1", node_id="n1", run_generation=generation)


def _run(generation: int) -> WorkflowRun:
    return WorkflowRun(id="r1", workflow_id="wf", generation=generation)


@pytest.fixture(autouse=True)
def _reset_mode():
    """Each test restores the settings value after it runs."""
    from app.layer4_frameworks.config import app_config
    original = app_config.settings.GENERATION_GUARD_MODE
    yield
    app_config.settings.GENERATION_GUARD_MODE = original


def _set_mode(mode: str) -> None:
    from app.layer4_frameworks.config import app_config
    app_config.settings.GENERATION_GUARD_MODE = mode


def test_matching_generation_passes():
    assert _reject(_task(3), _run(3), DummyLogger(), "complete") is False


def test_stale_task_generation_rejects_in_on_mode():
    _set_mode("on")
    assert _reject(_task(2), _run(3), DummyLogger(), "complete") is True


def test_stale_task_generation_passes_in_shadow_mode():
    _set_mode("shadow")
    logger = DummyLogger()
    assert _reject(_task(2), _run(3), logger, "complete") is False
    assert any("generation mismatch" in msg for _, msg in logger.messages)


def test_stale_task_generation_passes_in_off_mode():
    _set_mode("off")
    logger = DummyLogger()
    assert _reject(_task(2), _run(3), logger, "complete") is False


def test_future_task_generation_rejects_and_logs_as_error():
    """Anomalous: task stamped with a generation the run never reached.
    Indicates a bug (e.g. task saved before run bump committed) — log
    at error level regardless of mode, still reject when ``on``."""
    _set_mode("on")
    logger = DummyLogger()
    assert _reject(_task(5), _run(3), logger, "complete") is True
    assert any(level == "ERROR" for level, _ in logger.messages)


def test_metric_emitted_on_mismatch():
    _set_mode("on")
    monitor = DummyMonitor()
    _reject(_task(2), _run(3), DummyLogger(), "fail", monitor)
    assert any(
        metric == "workflow.task.callback.superseded_generation"
        for metric, _ in monitor.metrics
    )


def test_unknown_mode_defaults_to_on():
    """Unknown values coerce to ``on`` — safer default for a correctness
    guard than silently passing everything."""
    _set_mode("totally-invalid")
    assert _reject(_task(2), _run(3), DummyLogger(), "complete") is True
