from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
APP = "smdg-ai-tenant-1-worker-jira-worker"


def _reg(worker_type, instance_id, hb="2026-07-28T02:00:00+00:00"):
    return WorkerRegistration(worker_type=worker_type, instance_id=instance_id,
                              health_status="healthy", endpoint_url=JIRA, last_heartbeat=hb)


def test_one_entry_per_process_not_per_node_type():
    store = MemoryWorkerRegistryStore()
    for t in ("jira_create_issue", "jira_get_issue", "jira_search"):
        store.register(_reg(t, "jira-worker_0"))
    store.register(_reg("jira_create_issue", "jira-worker_1"))

    rows = store.list_app_instances(APP)
    assert len(rows) == 2
    by_instance = {reg.instance_id: nodes for reg, nodes in rows}
    assert sorted(by_instance["jira-worker_0"]) == ["jira_create_issue", "jira_get_issue", "jira_search"]
    assert by_instance["jira-worker_1"] == ["jira_create_issue"]


def test_freshest_registration_represents_the_process():
    # jira-worker_0 registers its STALE row FIRST and its FRESH row LAST. With
    # this ordering, first-seen-wins would ALSO keep the stale row (it is
    # already the representative before the fresh row ever arrives), so the
    # only way to end up with the fresh row as representative is for the
    # replace branch (`if parse_ts(w...) > parse_ts(reg...): folded[...] = ...`)
    # to actually fire. list_app_instances must compare via parse_ts and keep
    # the freshest registration as the representative regardless of write
    # order, while still folding in every node type the process serves.
    store = MemoryWorkerRegistryStore()
    stale_hb = "2026-07-28T01:00:00+00:00"
    fresh_hb = "2026-07-28T05:00:00+00:00"
    store.register(_reg("jira_create_issue", "jira-worker_0", hb=stale_hb))
    store.register(_reg("jira_get_issue", "jira-worker_0", hb=fresh_hb))

    rows = store.list_app_instances(APP)
    assert len(rows) == 1
    reg, nodes = rows[0]
    assert reg.last_heartbeat == fresh_hb
    assert reg.worker_type == "jira_get_issue"  # the field that differs between the two rows
    assert sorted(nodes) == ["jira_create_issue", "jira_get_issue"]


def test_unknown_app_returns_empty():
    assert MemoryWorkerRegistryStore().list_app_instances("nope") == []


def test_blank_app_returns_empty():
    assert MemoryWorkerRegistryStore().list_app_instances("") == []
