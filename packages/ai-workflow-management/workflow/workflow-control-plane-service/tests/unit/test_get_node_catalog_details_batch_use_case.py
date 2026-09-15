"""Tests for GetNodeCatalogDetailsBatchUseCase — per-ref dispatch + cap."""

import pytest

from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
    GetNodeCatalogDetailCommand,
    GetNodeCatalogDetailResult,
)
from app.layer2_application.features.get_node_catalog_details_batch import (
    GetNodeCatalogDetailsBatchUseCase,
)
from app.layer2_application.features.get_node_catalog_details_batch.use_cases.get_node_catalog_details_batch_use_case import (
    BatchSizeExceededError,
    GetNodeCatalogDetailsBatchCommand,
    MAX_REFS_PER_BATCH,
    NodeRef,
)
from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeRuntime,
    NodeVisibility,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _detail(id_: str):
    return NodeDefinitionDetail(
        id=id_,
        name=id_,
        description="",
        provider=id_.split(":", 1)[0],  # type: ignore[arg-type]
        node_type="TASK",
        category="cat",
        icon="Cog",
        color="#000",
        tags=[],
        spec_version="1.0.0",
        visibility=NodeVisibility.VISIBLE,
        runtime=NodeRuntime.ENGINE,
    )


class _FakeDetailUseCase:
    def __init__(self, store: dict[str, NodeDefinitionDetail] | None = None):
        self._store = store or {}
        self.calls: list[GetNodeCatalogDetailCommand] = []

    async def execute(self, command: GetNodeCatalogDetailCommand) -> GetNodeCatalogDetailResult:
        self.calls.append(command)
        d = self._store.get(command.node_id)
        return GetNodeCatalogDetailResult(
            detail=d,
            etag='W/"x"' if d else "",
        )


def _make_uc(store=None) -> GetNodeCatalogDetailsBatchUseCase:
    uc = GetNodeCatalogDetailsBatchUseCase(logger=DummyLogger())
    uc._detail_uc = _FakeDetailUseCase(store or {})
    return uc


class TestBatchSuccess:
    @pytest.mark.asyncio
    async def test_returns_all_matching_details(self):
        uc = _make_uc({
            "builtin:a": _detail("builtin:a"),
            "worker:b": _detail("worker:b"),
        })
        r = await uc.execute(GetNodeCatalogDetailsBatchCommand(
            refs=[NodeRef("builtin:a"), NodeRef("worker:b")],
        ))
        assert len(r.details) == 2
        assert len(r.missing) == 0
        assert {d.id for d in r.details} == {"builtin:a", "worker:b"}

    @pytest.mark.asyncio
    async def test_empty_refs_returns_empty_result(self):
        uc = _make_uc()
        r = await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[]))
        assert r.details == []
        assert r.missing == []


class TestPartialSuccess:
    @pytest.mark.asyncio
    async def test_missing_refs_reported_separately(self):
        uc = _make_uc({"builtin:a": _detail("builtin:a")})
        r = await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[
            NodeRef("builtin:a"),
            NodeRef("builtin:missing"),
            NodeRef("worker:also-missing"),
        ]))
        assert len(r.details) == 1
        assert r.details[0].id == "builtin:a"
        assert {m.node_id for m in r.missing} == {"builtin:missing", "worker:also-missing"}


class TestBatchSizeCap:
    @pytest.mark.asyncio
    async def test_over_max_raises(self):
        uc = _make_uc()
        refs = [NodeRef(f"builtin:n{i}") for i in range(MAX_REFS_PER_BATCH + 1)]
        with pytest.raises(BatchSizeExceededError):
            await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=refs))

    @pytest.mark.asyncio
    async def test_at_max_succeeds(self):
        uc = _make_uc()
        refs = [NodeRef(f"builtin:n{i}") for i in range(MAX_REFS_PER_BATCH)]
        r = await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=refs))
        assert len(r.missing) == MAX_REFS_PER_BATCH


class TestSpecVersionThrough:
    @pytest.mark.asyncio
    async def test_spec_version_passed_to_detail_uc(self):
        uc = _make_uc({"builtin:a": _detail("builtin:a")})
        await uc.execute(GetNodeCatalogDetailsBatchCommand(
            refs=[NodeRef("builtin:a", spec_version="1.0.0")],
        ))
        calls = uc._detail_uc.calls  # type: ignore[attr-defined]
        assert calls[0].spec_version == "1.0.0"


class TestETag:
    @pytest.mark.asyncio
    async def test_etag_present_when_details_found(self):
        uc = _make_uc({"builtin:a": _detail("builtin:a")})
        r = await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[NodeRef("builtin:a")]))
        assert r.etag.startswith('W/"')


class TestUnwiredError:
    @pytest.mark.asyncio
    async def test_without_detail_uc_raises(self):
        uc = GetNodeCatalogDetailsBatchUseCase(logger=DummyLogger())
        # Deliberately don't wire _detail_uc.
        with pytest.raises(RuntimeError, match="not wired"):
            await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[NodeRef("builtin:a")]))
