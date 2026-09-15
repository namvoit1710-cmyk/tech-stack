"""SA-2053 (R6) — GET /workers/replicas: the per-type aggregate."""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer2_application.features.list_worker_replicas.use_cases.list_worker_replicas_usecase import (
    ListWorkerReplicasUseCase, representative_mode,
)

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


class _Cfg:
    WORKER_STALE_AFTER_SECONDS = 120


def _worker(instance_id, worker_type="jira_search", *, age_seconds=10, status="healthy", mode="push"):
    hb = "" if age_seconds is None else (NOW - timedelta(seconds=age_seconds)).isoformat()
    return WorkerRegistration(
        instance_id=instance_id, worker_type=worker_type, health_status=status,
        last_heartbeat=hb, host="10.0.0.1", version="1.0.0", sdk_version="0.9.0",
        delivery_mode=mode,
    )


def _uc(*workers, store=None):
    store = store or MemoryWorkerRegistryStore()
    for w in workers:
        store.register(w)
    return ListWorkerReplicasUseCase(logger=_Log(), registry_store=store, app_config=_Cfg())


def _by_type(rows):
    return {r["worker_type"]: r for r in rows}


def test_counts_come_from_replica_summary():
    uc = _uc(_worker("w_0"), _worker("w_1"), _worker("w_2"), _worker("x_0", worker_type="slack_post"))
    rows = _by_type(uc.execute(now=NOW))
    assert rows["jira_search"]["replicas_total"] == 3
    assert rows["jira_search"]["replicas_healthy"] == 3
    assert rows["slack_post"]["replicas_total"] == 1


def test_an_unhealthy_replica_lowers_healthy_but_not_total():
    uc = _uc(_worker("w_0"), _worker("w_1"), _worker("w_2", status="unhealthy"))
    row = _by_type(uc.execute(now=NOW))["jira_search"]
    assert (row["replicas_total"], row["replicas_healthy"]) == (3, 2)


def test_delivery_mode_is_the_wire_value():
    """RC-11 — str(DeliveryMode.PULL) would emit 'DeliveryMode.PULL'."""
    uc = _uc(_worker("w_0", mode="pull"))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["delivery_mode"] == "pull"


def test_delivery_mode_follows_the_freshest_healthy_instance():
    """Rolling deploy: two versions of a type disagree. The freshest healthy one
    represents the type -- the same rule SA-2052's palette uses, so the two
    surfaces cannot contradict each other."""
    uc = _uc(_worker("old", age_seconds=90, mode="push"), _worker("new", age_seconds=5, mode="pull"))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["delivery_mode"] == "pull"


def test_delivery_mode_falls_back_to_the_freshest_of_all_when_none_are_healthy():
    """Fallback step 2: every replica dead but not yet GC'd -- the type is still
    listed, and its mode comes from the freshest corpse rather than an exception."""
    uc = _uc(_worker("a", age_seconds=400, status="unhealthy", mode="push"),
             _worker("b", age_seconds=200, status="unhealthy", mode="pull"))
    row = _by_type(uc.execute(now=NOW))["jira_search"]
    assert row["delivery_mode"] == "pull"
    assert (row["replicas_total"], row["replicas_healthy"]) == (2, 0)


def test_representative_mode_on_an_empty_pool_is_blank_not_push():
    """Fallback step 3. '' means UNDETERMINABLE. 'push' would be a fabricated value
    indistinguishable from a real push worker -- which is exactly what
    normalize_delivery_mode('') returns, so it must NOT be on the output path."""
    assert representative_mode([]) == ""


def test_empty_pool_race_yields_blank_mode_and_no_exception():
    """SA-2053 §5.2: replica_summary() and list_instances() take the store lock
    SEPARATELY, so R4's sweeper can remove a type's last row between them. This
    branch is REACHABLE -- do not delete it as unreachable."""

    class _RacyStore:
        def replica_summary(self):
            return {"jira_search": {"total": 1, "healthy": 1, "unhealthy": 0}}

        def list_instances(self, worker_type):
            return []          # the sweeper removed the last row between the two calls

    uc = ListWorkerReplicasUseCase(logger=_Log(), registry_store=_RacyStore(), app_config=_Cfg())
    row = _by_type(uc.execute(now=NOW))["jira_search"]
    assert row["delivery_mode"] == ""
    assert row["replicas_total"] == 1     # the summary's snapshot is still reported


def test_empty_store_returns_empty_list():
    uc = _uc()
    assert uc.execute(now=NOW) == []


# ======================================================================
# Q13 — oldest_heartbeat_age_seconds
# ======================================================================

def test_oldest_age_is_the_worst_staleness_among_live_instances():
    uc = _uc(_worker("w_0", age_seconds=10), _worker("w_1", age_seconds=75))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] == 75.0


def test_an_unhealthy_corpse_does_not_dominate_the_metric():
    """Q13's whole point: a marked-unhealthy row lingers up to
    WORKER_REMOVE_AFTER_SECONDS (600). Counting it would pin the number near 600 and
    stop it responding to the live replicas."""
    uc = _uc(_worker("w_0", age_seconds=10), _worker("w_1", age_seconds=40),
             _worker("dead", age_seconds=550, status="unhealthy"))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] == 40.0


def test_an_unhealthy_instance_within_the_stale_window_is_excluded_by_status_not_age():
    """Isolates the health_status filter from the age filter. 'bad' is unhealthy but its
    age (50s) is well inside the 120s stale window -- the age check alone would keep it
    in the pool. Delete `if w.health_status not in LIVE_STATUSES: continue` and 'bad'
    wins the max, reporting 50.0 instead of the healthy instance's 10.0."""
    uc = _uc(_worker("w_0", age_seconds=10), _worker("bad", age_seconds=50, status="unhealthy"))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] == 10.0


def test_a_still_healthy_but_stale_instance_is_excluded():
    """The pre-sweep window: status is still 'healthy' but the age is past the stale
    threshold. Excluded -- the metric describes the measurably live."""
    uc = _uc(_worker("w_0", age_seconds=10), _worker("silent", age_seconds=300, status="healthy"))
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] == 10.0


def test_a_never_heartbeated_instance_counts_as_healthy_but_not_in_the_age_metric():
    """The deliberate divergence (spec §6.1). HEALTHY_STATUSES admits blank status
    and count_healthy uses it; Q13's LIVE_STATUSES does not, and an unmeasurable
    heartbeat cannot be the worst measurement."""
    uc = _uc(_worker("w_0", age_seconds=30), _worker("fresh_boot", age_seconds=None))
    row = _by_type(uc.execute(now=NOW))["jira_search"]
    assert row["replicas_healthy"] == 2
    assert row["oldest_heartbeat_age_seconds"] == 30.0


def test_empty_population_is_null_not_zero():
    """0 reads as 'every replica just heartbeated' -- the exact opposite of the truth."""
    uc = _uc(_worker("dead", age_seconds=550, status="unhealthy"))
    row = _by_type(uc.execute(now=NOW))["jira_search"]
    assert row["oldest_heartbeat_age_seconds"] is None
    assert row["oldest_heartbeat_age_seconds"] != 0


def test_stale_window_zero_is_honored_not_defaulted():
    """WORKER_STALE_AFTER_SECONDS=0 means 'nothing is fresh'. A `getattr(...) or 120`
    would swallow the 0 and silently use 120 -- the SA-2052 Minor #4 defect."""
    class _ZeroCfg:
        WORKER_STALE_AFTER_SECONDS = 0

    store = MemoryWorkerRegistryStore()
    store.register(_worker("w_0", age_seconds=5))
    uc = ListWorkerReplicasUseCase(logger=_Log(), registry_store=store, app_config=_ZeroCfg())
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] is None


def test_missing_app_config_defaults_to_120():
    store = MemoryWorkerRegistryStore()
    store.register(_worker("w_0", age_seconds=100))
    store.register(_worker("w_1", age_seconds=200))
    uc = ListWorkerReplicasUseCase(logger=_Log(), registry_store=store, app_config=None)
    assert _by_type(uc.execute(now=NOW))["jira_search"]["oldest_heartbeat_age_seconds"] == 100.0
