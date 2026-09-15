from datetime import datetime, timezone

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer2_application.features.list_app_instances.use_cases.list_app_instances_usecase import (
    ListAppInstancesInput, ListAppInstancesUseCase,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
APP = "smdg-ai-tenant-1-worker-jira-worker"
NOW = datetime(2026, 7, 28, 2, 0, 0, tzinfo=timezone.utc)


class _Logger:
    def info(self, *a, **k): pass


def test_one_row_per_process_carrying_its_nodes():
    store = MemoryWorkerRegistryStore()
    # Registered DESCENDING (get before create) so the store's fold hands the use
    # case an unsorted list -- if the use case ever drops its own sort, this
    # produces ["jira_get_issue", "jira_create_issue"] and the assertion below
    # (compared unsorted, deliberately) catches it.
    for t in ("jira_get_issue", "jira_create_issue"):
        store.register(WorkerRegistration(worker_type=t, instance_id="jira-worker_0", health_status="healthy",
                                          endpoint_url=JIRA, last_heartbeat="2026-07-28T01:59:55+00:00",
                                          version="0.1.0", delivery_mode="push"))
    rows = ListAppInstancesUseCase(logger=_Logger(), registry_store=store).execute(
        ListAppInstancesInput(worker_app=APP), now=NOW)
    assert len(rows) == 1
    assert rows[0]["instance_id"] == "jira-worker_0"
    assert rows[0]["age_seconds"] == 5.0
    assert rows[0]["nodes"] == ["jira_create_issue", "jira_get_issue"]


def test_blank_app_returns_empty():
    rows = ListAppInstancesUseCase(logger=_Logger(), registry_store=MemoryWorkerRegistryStore()).execute(
        ListAppInstancesInput(worker_app=""), now=NOW)
    assert rows == []
