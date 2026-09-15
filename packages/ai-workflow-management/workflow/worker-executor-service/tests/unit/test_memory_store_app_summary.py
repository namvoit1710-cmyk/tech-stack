from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"


def _reg(worker_type, instance_id="", status="healthy", endpoint=JIRA, worker_app=""):
    return WorkerRegistration(worker_type=worker_type, instance_id=instance_id,
                              health_status=status, endpoint_url=endpoint, worker_app=worker_app)


def test_folds_many_node_types_into_one_app():
    store = MemoryWorkerRegistryStore()
    for t in ("jira_create_issue", "jira_get_issue", "jira_search"):
        store.register(_reg(t, instance_id="jira-worker_0"))
    summary = store.app_summary()
    assert list(summary) == ["smdg-ai-tenant-1-worker-jira-worker"]
    row = summary["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["nodes_total"] == 3
    assert row["nodes_healthy"] == 3
    assert row["node_types"] == ["jira_create_issue", "jira_get_issue", "jira_search"]  # sorted
    assert row["replicas_total"] == 1        # one PROCESS, three node types
    assert row["replicas_healthy"] == 1
    assert row["all_synthesized"] is False


def test_two_processes_of_one_app_count_as_two_replicas_but_one_node():
    """Scaling a deployable adds replicas, never node types.

    Counting registrations here is what made jira-worker read "44 nodes" on two
    instances while it still offered 22 (witnessed live, R14 evidence note).
    """
    store = MemoryWorkerRegistryStore()
    for inst in ("jira-worker_0", "jira-worker_1"):
        store.register(_reg("jira_create_issue", instance_id=inst))
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["replicas_total"] == 2
    assert row["nodes_total"] == 1           # ONE node type, served twice
    assert row["node_types"] == ["jira_create_issue"]


def test_node_count_is_invariant_under_replica_count():
    """The regression guard: same catalogue, more processes, same node count."""
    store = MemoryWorkerRegistryStore()
    types = ("jira_create_issue", "jira_get_issue", "jira_search")
    for t in types:
        store.register(_reg(t, instance_id="jira-worker_0"))
    one_replica = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]["nodes_total"]
    for t in types:
        store.register(_reg(t, instance_id="jira-worker_1"))
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["replicas_total"] == 2
    assert row["nodes_total"] == one_replica == 3


def test_all_synthesized_when_no_instance_ids_supplied():
    store = MemoryWorkerRegistryStore()
    for t in ("jira_create_issue", "jira_get_issue"):
        store.register(_reg(t))              # no instance_id -> uuid4 per registration
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["all_synthesized"] is True
    assert row["replicas_total"] == 0        # nothing countable
    assert row["nodes_total"] == 2


def test_unhealthy_registration_excluded_from_nodes_healthy():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("jira_create_issue", instance_id="jira-worker_0"))
    store.register(_reg("jira_get_issue", instance_id="jira-worker_0", status="unhealthy"))
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["nodes_total"] == 2
    assert row["nodes_healthy"] == 1


def test_node_type_is_healthy_when_any_replica_serves_it():
    """One live replica keeps the type servable, so it must not read as down."""
    store = MemoryWorkerRegistryStore()
    store.register(_reg("jira_create_issue", instance_id="jira-worker_0", status="unhealthy"))
    store.register(_reg("jira_create_issue", instance_id="jira-worker_1"))
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["nodes_total"] == 1
    assert row["nodes_healthy"] == 1
    assert row["replicas_total"] == 2
    assert row["replicas_healthy"] == 1


def test_partially_synthesized_app_counts_only_the_real_process():
    store = MemoryWorkerRegistryStore()
    store.register(_reg("jira_create_issue", instance_id="jira-worker_0"))  # real
    store.register(_reg("jira_get_issue"))                                  # synthesized
    row = store.app_summary()["smdg-ai-tenant-1-worker-jira-worker"]
    assert row["all_synthesized"] is False
    assert row["replicas_total"] == 1        # only the real process counts
    assert row["replicas_healthy"] == 1
    assert row["nodes_total"] == 2           # two distinct node types either way
