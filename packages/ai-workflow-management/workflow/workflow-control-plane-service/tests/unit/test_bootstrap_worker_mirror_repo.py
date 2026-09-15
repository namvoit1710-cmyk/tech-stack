"""SA-2050 (R3) — _build_worker_mirror_repo returns a HANA repo when infra.db
exists, else None (mock / no HANA → the reconcile loop no-ops). SimpleNamespace
stubs, no boot — mirrors test_bootstrap_event_log_selection.py.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.layer4_frameworks.persistence.hana.hana_worker_registry_mirror_repo import (
    HanaWorkerRegistryMirrorRepository,
)
from bootstrap import _build_worker_mirror_repo


def test_no_infra_returns_none():
    assert _build_worker_mirror_repo(SimpleNamespace(), infra=None) is None


def test_infra_db_none_returns_none_mock_mode():
    assert _build_worker_mirror_repo(SimpleNamespace(), infra=SimpleNamespace(db=None)) is None


def test_with_infra_db_returns_hana_repo():
    fake_db = object()
    repo = _build_worker_mirror_repo(SimpleNamespace(), infra=SimpleNamespace(db=fake_db))
    assert isinstance(repo, HanaWorkerRegistryMirrorRepository)
