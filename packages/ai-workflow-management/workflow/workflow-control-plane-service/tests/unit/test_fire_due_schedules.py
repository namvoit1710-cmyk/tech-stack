"""Unit tests for the schedule (crontab) trigger scheduler.

``now`` is injected into ``execute`` so the due-check is fully deterministic —
no wall-clock, no sleeps. Covers: baseline-on-first-sight (no backfire),
fire-when-boundary-passes, no-double-fire-within-a-period, skip non-schedule /
invalid-cron, and the StartRunCommand shape.
"""
from types import SimpleNamespace

from croniter import croniter

from app.layer2_application.features.fire_due_schedules.use_cases.fire_due_schedules_usecase import (
    FireDueSchedulesUseCase,
)
from app.layer2_application.repositories.workflow_repository_interface import (
    TriggerRoute,
)


class _Logger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _StartRun:
    """Records every StartRunCommand it receives; returns a success result."""
    def __init__(self):
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return SimpleNamespace(id=f"run-{len(self.commands)}", error=None)


class _Repo:
    """Stands in for the real ``list_trigger_routes``, filter included.

    The type filter belongs to the repository (a stale DRAFT cron row must
    never reach this use case at all — see
    ``tests/unit/test_trigger_routes_published_only.py``), so the fake applies
    it too. ``asked_for`` records the argument, which is what
    ``test_asks_the_repo_for_schedule_routes_only`` asserts on — otherwise the
    fake's own filtering would make that property untestable here.
    """

    def __init__(self, *routes):
        self._routes = list(routes)
        self.asked_for = []

    def list_trigger_routes(self, trigger_types=()):
        self.asked_for.append(trigger_types)
        return [r for r in self._routes
                if not trigger_types or r.trigger_type in trigger_types]


def _repo(*routes):
    return _Repo(*routes)


def _route(wf_id="wf1", node_id="trigger", trigger_type="schedule",
           cron="* * * * *"):
    return TriggerRoute(
        workflow_id=wf_id, version="v1", node_id=node_id,
        trigger_type=trigger_type, schedule_cron=cron or "",
    )


def _uc(repo, start_run=None):
    return FireDueSchedulesUseCase(
        logger=_Logger(), workflow_repo=repo, start_run=start_run or _StartRun()
    )


T0 = 1_780_892_130.0  # arbitrary fixed epoch, not on a minute boundary


async def test_baselines_on_first_sight_and_does_not_backfire():
    sr = _StartRun()
    uc = _uc(_repo(_route()), sr)
    fired = await uc.execute(now_epoch=T0)
    assert fired == 0
    assert sr.commands == []


async def test_fires_when_a_cron_boundary_passes():
    sr = _StartRun()
    uc = _uc(_repo(_route()), sr)
    nxt = croniter("* * * * *", T0).get_next(float)  # first minute boundary after T0
    await uc.execute(now_epoch=T0)            # baseline
    assert await uc.execute(now_epoch=nxt - 0.1) == 0   # not yet
    assert await uc.execute(now_epoch=nxt + 0.1) == 1   # boundary passed -> fire
    assert len(sr.commands) == 1


async def test_does_not_double_fire_within_the_same_period():
    sr = _StartRun()
    uc = _uc(_repo(_route()), sr)
    nxt = croniter("* * * * *", T0).get_next(float)
    await uc.execute(now_epoch=T0)
    await uc.execute(now_epoch=nxt + 0.1)     # fires once
    again = await uc.execute(now_epoch=nxt + 0.2)  # same period, already rescheduled
    assert again == 0
    assert len(sr.commands) == 1


async def test_collapses_missed_boundaries_into_one_fire():
    """After downtime spanning several boundaries, fire once, not N times."""
    sr = _StartRun()
    uc = _uc(_repo(_route()), sr)
    await uc.execute(now_epoch=T0)
    fired = await uc.execute(now_epoch=T0 + 600)  # ~10 minute boundaries elapsed
    assert fired == 1


async def test_skips_non_schedule_trigger():
    sr = _StartRun()
    uc = _uc(_repo(_route(trigger_type="manual")), sr)
    nxt = croniter("* * * * *", T0).get_next(float)
    await uc.execute(now_epoch=T0)
    assert await uc.execute(now_epoch=nxt + 5) == 0
    assert sr.commands == []


async def test_asks_the_repo_for_schedule_routes_only():
    """The type filter is pushed into the repository, so this pins that the
    use case actually asks for it — a caller that passed ``()`` would receive
    every event route and try to cron-schedule them."""
    repo = _repo(_route())
    await _uc(repo).execute(now_epoch=T0)
    assert repo.asked_for == [("schedule",)]


async def test_skips_schedule_trigger_without_cron():
    sr = _StartRun()
    uc = _uc(_repo(_route(cron=None)), sr)
    await uc.execute(now_epoch=T0)
    assert await uc.execute(now_epoch=T0 + 600) == 0
    assert sr.commands == []


async def test_invalid_cron_is_skipped_not_raised():
    sr = _StartRun()
    uc = _uc(_repo(_route(cron="not a cron")), sr)
    # Must not raise; returns 0 fires across multiple ticks.
    assert await uc.execute(now_epoch=T0) == 0
    assert await uc.execute(now_epoch=T0 + 600) == 0
    assert sr.commands == []


async def test_start_run_command_shape():
    sr = _StartRun()
    uc = _uc(_repo(_route(wf_id="wf-sched")), sr)
    nxt = croniter("* * * * *", T0).get_next(float)
    await uc.execute(now_epoch=T0)
    await uc.execute(now_epoch=nxt + 0.1)
    cmd = sr.commands[0]
    assert cmd.workflow_id == "wf-sched"
    assert cmd.tenant_id == "system"
    assert cmd.metadata.get("trigger") == "schedule"
    assert cmd.metadata.get("schedule_cron") == "* * * * *"


async def test_multiple_workflows_independent_schedules():
    sr = _StartRun()
    repo = _repo(_route(wf_id="a"), _route(wf_id="b"))
    uc = _uc(repo, sr)
    nxt = croniter("* * * * *", T0).get_next(float)
    await uc.execute(now_epoch=T0)
    fired = await uc.execute(now_epoch=nxt + 0.1)
    assert fired == 2
    assert {c.workflow_id for c in sr.commands} == {"a", "b"}
