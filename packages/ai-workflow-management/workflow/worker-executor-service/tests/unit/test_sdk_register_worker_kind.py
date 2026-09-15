"""Tests for the additive worker `kind` field (SA-1734 sub-task A, Task 5).

Mirrors the existing `spec_version` handling: `kind` threads register → store →
discovery. These tests need NO grpc/proto — they exercise the entity, the SDK
register use case, the memory store round-trip, and the list_workers dict
serialization that feeds the gRPC discovery mapping.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.value_objects.node_kind import NodeKind
from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
    SdkRegisterWorkerInput,
    SdkRegisterWorkerUseCase,
)
from app.layer2_application.features.list_workers.use_cases.list_workers_usecase import (
    ListWorkersUseCase,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class TestEntityDefault:
    def test_kind_defaults_to_action(self):
        w = WorkerRegistration(worker_type="w1")
        assert w.kind == "action"

    def test_kind_can_be_set(self):
        w = WorkerRegistration(worker_type="w1", kind="read")
        assert w.kind == "read"


class TestRegisterStoreRoundTrip:
    def _uc_and_store(self):
        store = MemoryWorkerRegistryStore()
        uc = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        return uc, store

    def test_kind_defaults_to_action_when_missing(self):
        """Empty kind on input should be normalized to 'action'."""
        uc, store = self._uc_and_store()
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", kind=""))
        workers = store.list_all()
        assert len(workers) == 1
        assert workers[0].kind == "action"

    def test_kind_passthrough_via_store(self):
        uc, store = self._uc_and_store()
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", kind="read"))
        workers = store.list_all()
        assert workers[0].kind == "read"

    def test_kind_visible_via_get(self):
        # NOTE: was "write" — an invented, invalid kind. SA-1734 blocker fix
        # made the executor validate `kind` against the real vocabulary
        # (trigger/action/read/logic/human/transform/util), so "write" now
        # raises ValueError. Using a real vocabulary member ("transform")
        # preserves the round-trip this test exists to prove.
        uc, store = self._uc_and_store()
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", kind="transform"))
        stored = store.list_all()[0]
        assert store.get(stored.id).kind == "transform"


class TestListWorkersDictSerialization:
    """The gRPC discovery mapping reads `w.get("kind")` from these dicts."""

    def test_list_workers_output_carries_kind(self):
        store = MemoryWorkerRegistryStore()
        reg = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        reg.execute(SdkRegisterWorkerInput(worker_type="w1", kind="read"))

        lister = ListWorkersUseCase(logger=DummyLogger(), registry_store=store)
        rows = lister.execute()
        assert len(rows) == 1
        assert rows[0]["kind"] == "read"

    def test_list_workers_output_defaults_kind(self):
        store = MemoryWorkerRegistryStore()
        reg = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        reg.execute(SdkRegisterWorkerInput(worker_type="w1"))

        lister = ListWorkersUseCase(logger=DummyLogger(), registry_store=store)
        rows = lister.execute()
        assert rows[0]["kind"] == "action"


class TestKindSerializesAsPlainStringNotEnumRepr:
    """App-layer fix (code-review follow-up): ListWorkersOutput.kind and the
    WorkerRegistration entity now carry the real ``NodeKind`` type instead of
    bare ``str`` end-to-end. Equality checks like ``rows[0]["kind"] == "read"``
    pass either way (NodeKind is a str subclass) and would NOT have caught the
    regression this fix guards against: a `str, Enum` member's default
    __str__/__format__ renders "NodeKind.READ", not "read" (verified live in a
    throwaway REPL: f"{NodeKind.READ}" == "NodeKind.READ", not "read").

    So these tests assert on the actual serialised wire value (what
    jsonable_encoder -- what FastAPI's default response path uses -- produces),
    not just Python-level equality.
    """

    def _rows_for(self, kind: str = "") -> list[dict]:
        store = MemoryWorkerRegistryStore()
        reg = SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store)
        reg.execute(SdkRegisterWorkerInput(worker_type="w1", kind=kind))
        lister = ListWorkersUseCase(logger=DummyLogger(), registry_store=store)
        return lister.execute()

    def test_carries_real_nodekind_not_bare_str(self):
        rows = self._rows_for("read")
        assert isinstance(rows[0]["kind"], NodeKind)
        assert rows[0]["kind"] is NodeKind.READ

    def test_explicit_kind_serialises_as_plain_string(self):
        from fastapi.encoders import jsonable_encoder

        rows = self._rows_for("read")
        encoded = jsonable_encoder(rows[0])
        assert encoded["kind"] == "read"
        dumped = json.dumps(encoded)
        assert '"kind": "read"' in dumped or '"kind":"read"' in dumped
        assert "NodeKind" not in dumped

    def test_blank_kind_serialises_as_plain_string(self):
        from fastapi.encoders import jsonable_encoder

        rows = self._rows_for("")
        encoded = jsonable_encoder(rows[0])
        assert encoded["kind"] == "action"
        dumped = json.dumps(encoded)
        assert '"kind": "action"' in dumped or '"kind":"action"' in dumped
        assert "NodeKind" not in dumped
