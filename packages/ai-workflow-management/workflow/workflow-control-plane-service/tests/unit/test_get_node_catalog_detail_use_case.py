"""Tests for GetNodeCatalogDetailUseCase — prefix dispatch + spec_version."""

import pytest

from app.layer2_application.features.get_node_catalog_detail import (
    GetNodeCatalogDetailUseCase,
)
from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
    GetNodeCatalogDetailCommand,
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


class _FakeProvider:
    def __init__(self, store: dict[str, NodeDefinitionDetail] | None = None, raise_on_version_mismatch: bool = False):
        self._store = store or {}
        self._raise = raise_on_version_mismatch

    def get_detail(self, node_id: str, spec_version=None):
        if node_id in self._store:
            d = self._store[node_id]
            if spec_version is not None and spec_version != d.spec_version and self._raise:
                raise ValueError(f"spec_version mismatch for {node_id}")
            return d
        return None


def _make_uc(builtin_store=None, worker_store=None, business_store=None):
    return GetNodeCatalogDetailUseCase(
        logger=DummyLogger(),
        builtin_node_provider=_FakeProvider(builtin_store),
        worker_node_provider=_FakeProvider(worker_store),
        business_node_provider=_FakeProvider(business_store),
    )


class TestPrefixDispatch:
    @pytest.mark.asyncio
    async def test_builtin_prefix_routes_to_builtin_provider(self):
        uc = _make_uc(builtin_store={"builtin:condition": _detail("builtin:condition")})
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:condition"))
        assert r.detail is not None
        assert r.detail.id == "builtin:condition"

    @pytest.mark.asyncio
    async def test_worker_prefix_routes_to_worker_provider(self):
        uc = _make_uc(worker_store={"worker:http": _detail("worker:http")})
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="worker:http"))
        assert r.detail.id == "worker:http"

    @pytest.mark.asyncio
    async def test_business_prefix_routes_to_business_provider(self):
        uc = _make_uc(business_store={"business:abc": _detail("business:abc")})
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="business:abc"))
        assert r.detail.id == "business:abc"

    @pytest.mark.asyncio
    async def test_unknown_prefix_returns_none(self):
        uc = _make_uc()
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="unknown:x"))
        assert r.detail is None

    @pytest.mark.asyncio
    async def test_empty_node_id_returns_none(self):
        uc = _make_uc()
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id=""))
        assert r.detail is None


class TestMissing:
    @pytest.mark.asyncio
    async def test_missing_ref_returns_none_detail(self):
        uc = _make_uc(builtin_store={})
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:never"))
        assert r.detail is None
        assert r.etag == ""


class TestSpecVersion:
    @pytest.mark.asyncio
    async def test_spec_version_passed_through_to_provider(self):
        captured: dict = {}

        class Capture:
            def get_detail(self, node_id, spec_version=None):
                captured["version"] = spec_version
                return _detail(node_id)

        uc = GetNodeCatalogDetailUseCase(
            logger=DummyLogger(),
            builtin_node_provider=Capture(),
            worker_node_provider=_FakeProvider(),
            business_node_provider=_FakeProvider(),
        )
        await uc.execute(GetNodeCatalogDetailCommand(
            node_id="builtin:x", spec_version="2.1.3",
        ))
        assert captured["version"] == "2.1.3"

    @pytest.mark.asyncio
    async def test_provider_version_error_propagates(self):
        uc = _make_uc(
            builtin_store={"builtin:x": _detail("builtin:x")},
            # re-construct with raise flag
        )
        # Swap in the raising provider.
        uc._providers["builtin:"] = _FakeProvider(
            store={"builtin:x": _detail("builtin:x")},
            raise_on_version_mismatch=True,
        )
        with pytest.raises(ValueError, match="spec_version"):
            await uc.execute(GetNodeCatalogDetailCommand(
                node_id="builtin:x", spec_version="9.9.9",
            ))


class TestETag:
    @pytest.mark.asyncio
    async def test_etag_present_on_success(self):
        uc = _make_uc(builtin_store={"builtin:x": _detail("builtin:x")})
        r = await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:x"))
        assert r.etag.startswith('W/"')

    @pytest.mark.asyncio
    async def test_different_details_different_etags(self):
        uc = _make_uc(builtin_store={
            "builtin:a": _detail("builtin:a"),
            "builtin:b": _detail("builtin:b"),
        })
        r_a = await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:a"))
        r_b = await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:b"))
        assert r_a.etag != r_b.etag
