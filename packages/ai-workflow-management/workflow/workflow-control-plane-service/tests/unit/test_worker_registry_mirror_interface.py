"""SA-2050 (R3) — the mirror repo port + ReconcileResult contract."""
from __future__ import annotations

from app.layer2_application.interfaces.worker_registry_mirror_repository_interface import (
    IWorkerRegistryMirrorRepository,
    ReconcileResult,
)


def test_reconcile_result_is_named_tuple():
    r = ReconcileResult(upserted=3, deleted=1)
    assert r.upserted == 3
    assert r.deleted == 1
    assert tuple(r) == (3, 1)


def test_interface_declares_reconcile_and_get_all():
    assert hasattr(IWorkerRegistryMirrorRepository, "reconcile")
    assert hasattr(IWorkerRegistryMirrorRepository, "get_all")
