"""SA-2053 (R6) — GET /workers/type/{t}/replicas: per-instance detail."""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer2_application.features.list_type_replicas.use_cases.list_type_replicas_usecase import (
    ListTypeReplicasUseCase, ListTypeReplicasInput,
)

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _worker(instance_id, worker_type="jira_search", *, age_seconds=10, status="healthy",
            host="10.0.0.1", version="1.2.3", sdk_version="0.9.0", mode="push"):
    hb = "" if age_seconds is None else (NOW - timedelta(seconds=age_seconds)).isoformat()
    return WorkerRegistration(
        instance_id=instance_id, worker_type=worker_type, health_status=status,
        last_heartbeat=hb, host=host, pid=42, started_at="2026-07-21T09:00:00+00:00",
        version=version, sdk_version=sdk_version, delivery_mode=mode,
    )


def _uc(*workers):
    store = MemoryWorkerRegistryStore()
    for w in workers:
        store.register(w)
    return ListTypeReplicasUseCase(logger=_Log(), registry_store=store)


def _run(uc, worker_type="jira_search"):
    return uc.execute(ListTypeReplicasInput(worker_type=worker_type), now=NOW)


def test_three_replicas_return_three_rows_with_distinct_instance_ids():
    uc = _uc(_worker("w_0"), _worker("w_1"), _worker("w_2"))
    rows = _run(uc)
    assert len(rows) == 3
    assert sorted(r["instance_id"] for r in rows) == ["w_0", "w_1", "w_2"]


def test_every_identity_field_round_trips():
    uc = _uc(_worker("w_0", host="10.9.9.9", version="7.7.7", sdk_version="3.3.3", mode="pull"))
    row = _run(uc)[0]
    assert row["host"] == "10.9.9.9"
    assert row["version"] == "7.7.7"
    assert row["sdk_version"] == "3.3.3"
    assert row["started_at"] == "2026-07-21T09:00:00+00:00"
    assert row["status"] == "healthy"


def test_delivery_mode_is_the_wire_value_not_the_enum_repr():
    """RC-11. str(DeliveryMode.PUSH) is 'DeliveryMode.PUSH' — shipping that on every
    row is the defect; a test written with str() would agree with it."""
    uc = _uc(_worker("w_0", mode="pull"))
    assert _run(uc)[0]["delivery_mode"] == "pull"


def test_age_seconds_is_measured_when_the_heartbeat_is_usable():
    uc = _uc(_worker("w_0", age_seconds=45))
    assert _run(uc)[0]["age_seconds"] == 45.0


def test_age_seconds_is_null_for_a_never_heartbeated_instance():
    """Never a number: now - datetime.min is ~6.4e10 s -> 'last seen 2,000 years ago'."""
    uc = _uc(_worker("w_0", age_seconds=None))
    row = _run(uc)[0]
    assert row["last_heartbeat"] == ""
    assert row["age_seconds"] is None


def test_age_seconds_is_null_for_an_unparseable_heartbeat():
    w = _worker("w_0")
    w.last_heartbeat = "not-a-timestamp"
    uc = _uc(w)
    assert _run(uc)[0]["age_seconds"] is None


def test_hana_space_form_heartbeat_yields_a_small_age():
    """SA-2058 regression guard at the API surface."""
    w = _worker("w_0")
    w.last_heartbeat = (NOW - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    uc = _uc(w)
    assert _run(uc)[0]["age_seconds"] == 30.0


def test_unhealthy_instances_are_still_listed():
    """This endpoint is the detail view — it shows corpses awaiting GC, on purpose."""
    uc = _uc(_worker("w_0"), _worker("w_1", status="unhealthy"))
    assert len(_run(uc)) == 2


def test_unknown_worker_type_returns_empty_list_not_an_error():
    uc = _uc(_worker("w_0"))
    result = uc.execute(ListTypeReplicasInput(worker_type="nope"), now=NOW)
    assert result == []


def test_blank_worker_type_returns_empty_list():
    uc = _uc(_worker("w_0"))
    assert uc.execute(ListTypeReplicasInput(worker_type=""), now=NOW) == []
