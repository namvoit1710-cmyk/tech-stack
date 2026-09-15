"""Guard for the schedule-scheduler BOOTSTRAP wiring.

The use-case unit tests inject their own logger stub, so they can NOT catch a
mistake in how ``bootstrap.py`` wires the use case. A real bug shipped this way:
the manual registration passed ``logger=_logger`` (stdlib ``logging.Logger``),
but the use case logs with structured kwargs (``logger.info(msg, workflow_id=...)``)
which stdlib logging rejects with ``TypeError: _log() got an unexpected keyword
argument 'workflow_id'`` — silently swallowed by the lifespan loop's handler.

This test builds the real container with the scheduler enabled and asserts the
use case got the structured DI logger, not the stdlib module logger. (A pure
behavioural ``logger.info(..., workflow_id=...)`` check is NOT reliable: stdlib
``Logger.info`` only reaches ``_log`` — and raises — when INFO is enabled, and
the test runner defaults to WARNING. The type assertion is level-independent.)
"""
import logging

import bootstrap
from bootstrap import build_app_container


def test_fire_due_schedules_registered_with_structured_logger(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "WORKFLOW_SCHEDULER_ENABLED", True)
    container = build_app_container()

    assert "fire_due_schedules" in container, (
        "scheduler use case must be registered when WORKFLOW_SCHEDULER_ENABLED is set"
    )
    uc = container["fire_due_schedules"]

    # The bug shipped as logger=_logger (stdlib logging.Logger), which rejects the
    # structured kwargs the use case logs with (logger.info(msg, workflow_id=...)).
    # The DI logger is the app StandardLogger — NOT a stdlib Logger.
    assert not isinstance(uc.logger, logging.Logger), (
        "scheduler must use the structured DI logger (infra.logger), not the stdlib "
        "module logger _logger — the use case logs with kwargs like workflow_id="
    )
    # And it accepts structured kwargs without raising:
    uc.logger.info("scheduler wiring smoke", workflow_id="x", node_id="n", run_id="r")


def test_fire_due_schedules_absent_when_flag_off(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "WORKFLOW_SCHEDULER_ENABLED", False)
    container = build_app_container()
    assert "fire_due_schedules" not in container, (
        "scheduler must stay unregistered (env gate) when the flag is off — "
        "auto-discovery must not pick it up"
    )


async def test_schedule_trigger_saved_via_parameters_fires(monkeypatch):
    """NodeSpec redesign: schedule_cron/trigger_type are re-homed to config
    by the spec-driven split. The FE path (POST /workflows with parameters)
    must still reach the scheduler — this guards the re-homing end to end."""
    import pytest
    from fastapi.testclient import TestClient
    from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
    from tests.conftest import assert_ok

    monkeypatch.setattr(bootstrap.settings, "WORKFLOW_SCHEDULER_ENABLED", True)
    container = build_app_container()
    client = TestClient(create_fastapi_app(container))

    body = assert_ok(client.post("/api/v1/workflows", json={
        "name": "wf-cron",
        "nodes": [{
            "id": "t1", "name": "T", "node_type": "TRIGGER",
            "parameters": {"trigger_type": "schedule",
                           "schedule_cron": "* * * * *"},
        }],
    }))
    wf_id = body["id"]
    # Scheduler only scans main flows.
    r = client.patch(f"/api/v1/workflows/{wf_id}/main-flow",
                     json={"main_flow": True})
    assert r.status_code == 200, r.text
    # ...and only PUBLISHED versions. Publish is the act that arms an
    # automatic trigger: this endpoint drives the full FE path, so it also
    # guards that a schedule saved via ``parameters`` survives the publish
    # round-trip into ``config``.
    r = client.post(f"/api/v1/workflows/publish/{wf_id}",
                    json={"version": body["version"], "is_confirm": True})
    assert r.status_code == 200, r.text

    uc = container["fire_due_schedules"]
    base = 1_750_000_000.0
    fired_first = await uc.execute(now_epoch=base)       # baseline tick, no fire
    fired_second = await uc.execute(now_epoch=base + 61)  # next minute boundary
    assert fired_first == 0
    assert fired_second == 1

    # Workflow creation also seeds a test run — filter to the fired one.
    runs = [
        r for r in container["_infra"].repos.run.list_by_workflow(wf_id)
        if (r.metadata or {}).get("trigger") == "schedule"
    ]
    assert len(runs) == 1
    assert runs[0].metadata.get("schedule_cron") == "* * * * *"
