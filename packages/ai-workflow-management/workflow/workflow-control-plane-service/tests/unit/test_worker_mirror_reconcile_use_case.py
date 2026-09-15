"""SA-2050 (R3) — WorkerMirrorReconcileUseCase guardrails: mock no-op,
empty-snapshot skip, non-empty reconcile, swallowed write errors. Fake client +
fake repo — no HANA, no registry. ``asyncio_mode = auto`` runs the async tests.
"""
from __future__ import annotations

from app.layer2_application.interfaces.worker_registry_mirror_repository_interface import (
    ReconcileResult,
)
from app.layer2_application.features.worker_mirror_reconcile import (
    WorkerMirrorReconcileUseCase,
)


class _FakeClient:
    def __init__(self, workers):
        self._workers = workers
        self.calls = 0

    def list_workers(self):
        self.calls += 1
        return self._workers


class _FakeRepo:
    def __init__(self, raise_on_reconcile=False):
        self.reconcile_calls = []
        self._raise = raise_on_reconcile

    def reconcile(self, rows):
        self.reconcile_calls.append(rows)
        if self._raise:
            raise RuntimeError("boom")
        return ReconcileResult(upserted=len(rows), deleted=0)

    def get_all(self):
        return []


def _uc(client=None, repo=None):
    uc = WorkerMirrorReconcileUseCase(worker_registry_client=client, logger=None)
    uc._repo = repo
    return uc


async def test_mock_mode_no_repo_is_noop():
    client = _FakeClient([{"id": "i1:jira_search", "worker_type": "jira_search"}])
    uc = _uc(client=client, repo=None)  # repo None = mock / no HANA
    result = await uc.execute()
    assert result["reconciled"] is False
    assert client.calls == 0  # short-circuits — never touches the registry


async def test_empty_snapshot_skips_reconcile():
    client = _FakeClient([])  # empty OR unreachable — indistinguishable (§6)
    repo = _FakeRepo()
    uc = _uc(client=client, repo=repo)
    result = await uc.execute()
    assert result["reconciled"] is False
    assert result["reason"] == "empty_snapshot"
    assert repo.reconcile_calls == []  # never reconcile on empty — can't wipe


async def test_non_empty_snapshot_calls_reconcile():
    rows = [
        {"id": "i1:jira_search", "worker_type": "jira_search"},
        {"id": "i2:jira_search", "worker_type": "jira_search"},
    ]
    client = _FakeClient(rows)
    repo = _FakeRepo()
    uc = _uc(client=client, repo=repo)
    result = await uc.execute()
    assert result == {"reconciled": True, "upserted": 2, "deleted": 0}
    assert repo.reconcile_calls == [rows]


async def test_write_error_is_swallowed():
    client = _FakeClient([{"id": "i1:jira_search", "worker_type": "jira_search"}])
    repo = _FakeRepo(raise_on_reconcile=True)
    uc = _uc(client=client, repo=repo)
    result = await uc.execute()  # must NOT raise — the loop must survive
    assert result["reconciled"] is False
    assert result["reason"] == "write_error"
