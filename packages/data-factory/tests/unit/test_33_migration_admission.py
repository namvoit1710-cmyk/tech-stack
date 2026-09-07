"""The memory ceiling that decides whether a migration is allowed to start.

Connections were never the resource that runs out first. Every job holds its columns
in a Polars frame inside one container, so enough concurrent migrations OOM the app -
and an OOM does not fail one job, it fails every job running at that moment, including
the ones that were nearly done. One refused migration is the cheap outcome.
"""

import pytest

from app.layer2_application.features.data_migration import df_migration_job as mod


class _Job:
    def __init__(self):
        self.job_id, self.status = "J1", "ACCEPTED"


def at_memory(monkeypatch, *readings):
    """Drive memory_used_pct() through a script of percentages."""
    seen = list(readings)

    def used():
        return seen.pop(0) if len(seen) > 1 else seen[0]

    monkeypatch.setattr(mod, "memory_used_pct", used)


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)


# --------------------------------------------------------------------- admission

def test_a_job_starts_immediately_when_there_is_room(monkeypatch):
    at_memory(monkeypatch, 20.0)
    job = _Job()
    mod.wait_for_memory(job)
    assert job.status == "ACCEPTED"      # untouched: it never had to wait


def test_a_job_waits_while_the_container_is_over_the_ceiling(monkeypatch):
    at_memory(monkeypatch, 92.0, 91.0, 40.0)
    job = _Job()
    mod.wait_for_memory(job)             # returns once memory drops
    assert job.status == "WAITING"       # and it did report being held


def test_waiting_is_visible_rather_than_looking_like_a_stall(monkeypatch):
    """A job held at the door must not be indistinguishable from a hung one - the
    status is what the SSE stream and /jobs/{id} both report."""
    at_memory(monkeypatch, 95.0, 30.0)
    job = _Job()
    mod.wait_for_memory(job)
    assert job.status == "WAITING"


def test_a_job_is_refused_with_a_reason_once_the_wait_runs_out(monkeypatch):
    at_memory(monkeypatch, 95.0)
    monkeypatch.setattr(mod, "ADMIT_WAIT_SEC", 0.0)
    with pytest.raises(mod.MemoryPressure) as caught:
        mod.wait_for_memory(_Job())
    message = str(caught.value)
    assert "95.0%" in message                              # what it saw
    assert f"{mod.MEMORY_CEILING_PCT:.0f}%" in message      # what the limit was
    assert "OOM" in message                                 # why it refused


def test_the_ceiling_is_exclusive_so_exactly_at_the_limit_still_waits(monkeypatch):
    """80% means 'under 80', not 'up to and including'. At the line, hold."""
    at_memory(monkeypatch, mod.MEMORY_CEILING_PCT)
    monkeypatch.setattr(mod, "ADMIT_WAIT_SEC", 0.0)
    with pytest.raises(mod.MemoryPressure):
        mod.wait_for_memory(_Job())


def test_just_under_the_ceiling_is_admitted(monkeypatch):
    at_memory(monkeypatch, mod.MEMORY_CEILING_PCT - 0.1)
    job = _Job()
    mod.wait_for_memory(job)
    assert job.status == "ACCEPTED"


# ------------------------------------------------------------------ what it reads

def test_memory_is_read_container_aware_not_host_aware():
    """In CF, psutil reports the host's memory, not the 9GB the app is limited to.
    Reading the host would let the gate admit jobs into a container that is full."""
    monkey = {"available_percent": 25.0}
    import app.layer4_frameworks.providers.adaptive_batching as ab
    assert mod._get_system_memory is ab._get_system_memory
    assert abs(mod.memory_used_pct() - (100.0 - mod._get_system_memory()
                                        ["available_percent"])) < 1.0


def test_the_ceiling_and_the_wait_are_tunable_without_a_code_change():
    import os
    assert mod.MEMORY_CEILING_PCT == float(os.environ.get("DF_MEMORY_CEILING_PCT", "80"))
    assert mod.ADMIT_WAIT_SEC == float(os.environ.get("DF_ADMIT_WAIT_SEC", "120"))


# ------------------------------------------------------------- where it sits

def test_the_gate_runs_before_a_connection_is_taken(monkeypatch):
    """A job queued at the door must not be sitting on a pooled connection while it
    waits - that would starve the jobs it is waiting for."""
    order = []
    monkeypatch.setattr(mod, "wait_for_memory", lambda job: order.append("gate"))
    monkeypatch.setattr(mod, "acquire_connection",
                        lambda *a, **k: order.append("connect") or _boom())

    def _boom():
        raise RuntimeError("stop here")

    job = mod.MigrationJob(job_id="J1")
    dispatch = _dispatch()
    mod.run_migration_job(dispatch, job)
    assert order[:2] == ["gate", "connect"]


def _dispatch():
    return mod.DispatchDto(
        job_id="J1",
        source=mod.DfSourceDto(
            connection=mod.DfConnectionDto(host="h", port=443, user="u",
                                           schema="s", password="p"),
            virtual_table="VT",
        ),
        rules=[],
        callback=mod.DfCallbackTargetDto(url="http://127.0.0.1:9/x", job_id="J1"),
    )
