"""SA-2047 R14 Task 6 -- app_summary()/list_app_instances() on the HANA store.

HanaWorkerRegistryStore has zero production callers today (bootstrap wires
MemoryWorkerRegistryStore), but the interface binds both stores, so the counting
rule must produce IDENTICAL results here as it does there for the same rows --
see tests/unit/test_memory_store_app_summary.py and
tests/unit/test_memory_store_app_instances.py, which this file mirrors against
the HANA store's own row-loading (SELECT * -> _row_to_entity, same as
list_all()/discover()) instead of the memory store's dict.

MockDB matches the double already used by test_hana_registry_store.py.
"""
from app.layer4_frameworks.persistence.hana.hana_worker_registry_store import HanaWorkerRegistryStore

JIRA = "https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com"
APP = "smdg-ai-tenant-1-worker-jira-worker"


class MockDB:
    def __init__(self, rows):
        self._rows = rows

    def execute_query(self, sql, params=()):
        return self._rows

    def execute_write(self, sql, params=()):  # pragma: no cover - reads must not write
        raise AssertionError("app_summary/list_app_instances must not write")


def _row(worker_type, instance_id, hb="2026-07-28T02:00:00+00:00", status="healthy"):
    return {
        "id": f"{instance_id}:{worker_type}",
        "name": worker_type,
        "capabilities": "[]",
        "endpoint_url": JIRA,
        "health_status": status,
        "last_heartbeat": hb,
        "ttl_seconds": 60,
        "worker_type": worker_type,
        "instance_id": instance_id,
    }


def test_app_summary_folds_three_node_types_from_one_instance_into_one_app():
    """The counting rule, once: 3 registrations of 1 process == 1 replica."""
    db = MockDB([
        _row("jira_create_issue", "jira-worker_0"),
        _row("jira_get_issue", "jira-worker_0"),
        _row("jira_search", "jira-worker_0"),
    ])
    store = HanaWorkerRegistryStore(db)

    summary = store.app_summary()

    assert list(summary) == [APP]
    row = summary[APP]
    assert row["nodes_total"] == 3       # 3 registrations
    assert row["nodes_healthy"] == 3
    assert row["replicas_total"] == 1    # 1 PROCESS, not 3
    assert row["replicas_healthy"] == 1
    assert row["all_synthesized"] is False


def test_list_app_instances_returns_one_entry_per_process_with_its_node_types():
    db = MockDB([
        _row("jira_create_issue", "jira-worker_0"),
        _row("jira_get_issue", "jira-worker_0"),
        _row("jira_create_issue", "jira-worker_1"),
    ])
    store = HanaWorkerRegistryStore(db)

    rows = store.list_app_instances(APP)

    assert len(rows) == 2
    by_instance = {reg.instance_id: nodes for reg, nodes in rows}
    assert sorted(by_instance["jira-worker_0"]) == ["jira_create_issue", "jira_get_issue"]
    assert by_instance["jira-worker_1"] == ["jira_create_issue"]


def test_list_app_instances_blank_app_returns_empty():
    db = MockDB([_row("jira_create_issue", "jira-worker_0")])
    store = HanaWorkerRegistryStore(db)

    assert store.list_app_instances("") == []


def test_row_to_entity_preserves_id_when_instance_id_is_consistent():
    """Regression for review Finding 3 (Task 6): the moment a row carries a
    non-empty instance_id, WorkerRegistration.__post_init__ DISCARDS the `id`
    loaded from the row's primary key and recomputes
    `id = f"{instance_id}:{worker_type}"` -- for every read path through
    `_row_to_entity` (get, list_all, discover), not just app_summary/
    list_app_instances. That's harmless here only because `_row()` builds a
    row whose stored `id` already matches that exact derived shape. A future
    migration that adds an INSTANCE_ID column without guaranteeing
    `ID == f"{instance_id}:{worker_type}"` in every stored row would make
    `get()`/`heartbeat()`/`deregister()` (which resolve rows via ids obtained
    the same way, e.g. register()'s `worker.id = existing.id` from
    get_by_worker_type()) silently target a row that no longer exists under
    that id. This test pins the consistent case so a future change to the
    derivation trips it.
    """
    row = _row("jira_create_issue", "jira-worker_0")
    assert row["id"] == "jira-worker_0:jira_create_issue"  # the row IS consistent
    db = MockDB([row])
    store = HanaWorkerRegistryStore(db)

    fetched = store.get(row["id"])

    assert fetched is not None
    assert fetched.id == row["id"]
    assert fetched.instance_id == "jira-worker_0"


def test_row_to_entity_treats_null_instance_id_and_worker_app_as_absent():
    """Regression for review Finding 2 (Task 6): once a migration adds the
    INSTANCE_ID/WORKER_APP columns, an existing row rehydrates the new
    columns as SQL NULL -- not an absent key. `row.get(k) or ""` must fall
    back for BOTH; `row.get(k, "")` only covers the key being absent and
    would let None flow into these str-typed fields.
    """
    row = _row("jira_create_issue", "jira-worker_0")
    row["instance_id"] = None
    row["worker_app"] = None
    db = MockDB([row])
    store = HanaWorkerRegistryStore(db)

    fetched = store.get(row["id"])

    assert fetched is not None
    assert isinstance(fetched.instance_id, str)
    assert isinstance(fetched.worker_app, str)
    # NULL instance_id must be treated exactly like an absent one: synthesized.
    assert fetched.instance_synthesized is True
    # NULL worker_app falls through to the endpoint-derived name, not None.
    assert fetched.worker_app == APP
