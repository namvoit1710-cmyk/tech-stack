from datetime import datetime, timezone

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer2_application.features.list_worker_apps.use_cases.list_worker_apps_usecase import (
    ListWorkerAppsUseCase,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
APP = "smdg-ai-tenant-1-worker-jira-worker"
NOW = datetime(2026, 7, 28, 2, 0, 0, tzinfo=timezone.utc)


class _Logger:
    def info(self, *a, **k): pass


def _store(*regs):
    s = MemoryWorkerRegistryStore()
    for r in regs:
        s.register(r)
    return s


def _reg(worker_type, instance_id="", status="healthy", hb="2026-07-28T01:59:50+00:00"):
    return WorkerRegistration(worker_type=worker_type, instance_id=instance_id, health_status=status,
                              endpoint_url=JIRA, last_heartbeat=hb, delivery_mode="push")


def test_one_row_per_app_with_real_instance_ids():
    uc = ListWorkerAppsUseCase(logger=_Logger(),
                               registry_store=_store(_reg("jira_create_issue", "jira-worker_0"),
                                                     _reg("jira_get_issue", "jira-worker_0")))
    rows = uc.execute(now=NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["worker_app"] == APP
    assert row["nodes_total"] == 2
    assert row["replicas_total"] == 1
    assert row["replica_identity"] == "known"
    assert row["delivery_mode"] == "push"
    assert row["oldest_heartbeat_age_seconds"] == 10.0


def test_pending_identity_reports_zero_counts():
    uc = ListWorkerAppsUseCase(logger=_Logger(),
                               registry_store=_store(_reg("jira_create_issue"), _reg("jira_get_issue")))
    row = uc.execute(now=NOW)[0]
    assert row["replica_identity"] == "pending"
    assert row["replicas_total"] == 0
    assert row["replicas_healthy"] == 0
    assert row["nodes_total"] == 2          # nodes are still countable
