"""Tests for ListNodeCatalogUseCase — aggregation + caching + ETag."""

import pytest

from app.layer2_application.features.list_node_catalog import ListNodeCatalogUseCase
from app.layer2_application.features.list_node_catalog.use_cases.list_node_catalog_use_case import (
    ListNodeCatalogCommand,
)
from app.layer2_application.contracts import (
    NodeDefinitionSummary,
    NodeRuntime,
    NodeVisibility,
)
from app.layer4_frameworks.caching import NodeCatalogCache


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _summary(id_: str, category: str = "cat.a", runtime: NodeRuntime = NodeRuntime.ENGINE):
    prefix = id_.split(":", 1)[0] if ":" in id_ else "builtin"
    provider = prefix if prefix in ("builtin", "worker", "business") else "builtin"
    return NodeDefinitionSummary(
        id=id_ if ":" in id_ else f"builtin:{id_}",
        name=id_,
        description="",
        provider=provider,  # type: ignore[arg-type]
        node_type="TASK",
        category=category,
        icon="Cog",
        color="#000",
        tags=[],
        spec_version="1.0.0",
        visibility=NodeVisibility.VISIBLE,
        runtime=runtime,
    )


class _FakeProvider:
    def __init__(self, summaries):
        self._summaries = summaries

    def list_summaries(self, tenant_id: str = "system"):
        return list(self._summaries)


def _make_uc(
    builtin=None, worker=None, business=None, cache=None,
) -> ListNodeCatalogUseCase:
    return ListNodeCatalogUseCase(
        logger=DummyLogger(),
        builtin_node_provider=_FakeProvider(builtin or []),
        worker_node_provider=_FakeProvider(worker or []),
        business_node_provider=_FakeProvider(business or []),
        node_catalog_cache=cache or NodeCatalogCache(),
    )


class TestAggregation:
    @pytest.mark.asyncio
    async def test_aggregates_all_three_providers(self):
        uc = _make_uc(
            builtin=[_summary("builtin:condition")],
            worker=[_summary("worker:http")],
            business=[_summary("business:email")],
        )
        result = await uc.execute(ListNodeCatalogCommand())
        ids = {s.id for s in result.definitions}
        assert ids == {"builtin:condition", "worker:http", "business:email"}
        assert result.total == 3

    @pytest.mark.asyncio
    async def test_generated_at_is_iso_utc(self):
        uc = _make_uc(builtin=[_summary("builtin:x")])
        result = await uc.execute(ListNodeCatalogCommand())
        assert "T" in result.generated_at
        assert result.generated_at.endswith("+00:00") or "Z" in result.generated_at


class TestCategoryTree:
    @pytest.mark.asyncio
    async def test_groups_ids_by_category(self):
        uc = _make_uc(
            builtin=[
                _summary("builtin:a", category="cat.x"),
                _summary("builtin:b", category="cat.y"),
                _summary("builtin:c", category="cat.x"),
            ],
        )
        result = await uc.execute(ListNodeCatalogCommand())
        assert set(result.category_tree.keys()) == {"cat.x", "cat.y"}
        assert result.category_tree["cat.x"] == ["builtin:a", "builtin:c"]
        assert result.category_tree["cat.y"] == ["builtin:b"]


class TestCategoriesMetadata:
    @pytest.mark.asyncio
    async def test_known_categories_use_registered_labels(self):
        uc = _make_uc(
            builtin=[
                _summary("builtin:condition", category="Core.logic"),
                _summary("worker:jira_read", category="Jira.read"),
            ],
        )
        result = await uc.execute(ListNodeCatalogCommand())
        # Top-level collections: Core sorts first (0), connectors at 10.
        assert result.categories["Core"].label == "Core"
        assert result.categories["Core"].order == 0
        assert result.categories["Jira"].label == "Jira"
        assert result.categories["Jira"].order == 10
        # Leaf <collection>.<kind> keys use the fixed kind label/order.
        assert result.categories["Core.logic"].label == "Logic & Flow"
        assert result.categories["Core.logic"].order == 4
        assert result.categories["Jira.read"].label == "Find & Read"
        assert result.categories["Jira.read"].order == 3

    @pytest.mark.asyncio
    async def test_parent_paths_are_emitted(self):
        """A leaf like ``a.b.c`` must also expose ``a`` and ``a.b``."""
        uc = _make_uc(builtin=[_summary("builtin:x", category="a.b.c")])
        result = await uc.execute(ListNodeCatalogCommand())
        assert set(result.categories.keys()) == {"a", "a.b", "a.b.c"}

    @pytest.mark.asyncio
    async def test_unknown_category_falls_back_to_humanized_label(self):
        uc = _make_uc(builtin=[_summary("builtin:x", category="zz_custom.weird_key")])
        result = await uc.execute(ListNodeCatalogCommand())
        assert result.categories["zz_custom.weird_key"].label == "Weird Key"
        # Still high-order so it sorts after registered keys.
        assert result.categories["zz_custom.weird_key"].order >= 9000

    @pytest.mark.asyncio
    async def test_keys_match_tree_leaves_plus_ancestors(self):
        uc = _make_uc(
            builtin=[_summary("builtin:a", category="Jira.read")],
            business=[_summary("business:b", category="Core.logic")],
        )
        result = await uc.execute(ListNodeCatalogCommand())
        # Every key in category_tree appears in categories; no extras beyond
        # the ancestor expansion of those leaves.
        leaves = set(result.category_tree.keys())
        assert leaves.issubset(set(result.categories.keys()))
        # Jira.read → {Jira, Jira.read}; Core.logic → {Core, Core.logic}.
        # Nothing else.
        assert set(result.categories.keys()) == {
            "Jira", "Jira.read", "Core", "Core.logic",
        }


class TestETagStability:
    @pytest.mark.asyncio
    async def test_same_content_same_etag(self):
        """ETag must be independent of provider ordering."""
        uc1 = _make_uc(
            builtin=[_summary("x"), _summary("y")],
            worker=[],
        )
        uc2 = _make_uc(
            builtin=[_summary("y"), _summary("x")],
            worker=[],
        )
        r1 = await uc1.execute(ListNodeCatalogCommand())
        r2 = await uc2.execute(ListNodeCatalogCommand())
        assert r1.etag == r2.etag

    @pytest.mark.asyncio
    async def test_content_change_changes_etag(self):
        uc1 = _make_uc(builtin=[_summary("x")])
        uc2 = _make_uc(builtin=[_summary("x"), _summary("y")])
        r1 = await uc1.execute(ListNodeCatalogCommand())
        r2 = await uc2.execute(ListNodeCatalogCommand())
        assert r1.etag != r2.etag


class TestCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_result_without_provider_call(self):
        """Second call should not re-query the providers."""
        summaries = [_summary("builtin:a")]

        call_count = {"n": 0}

        class CountingProvider:
            def list_summaries(self, tenant_id="system"):
                call_count["n"] += 1
                return list(summaries)

        cache = NodeCatalogCache()
        uc = ListNodeCatalogUseCase(
            logger=DummyLogger(),
            builtin_node_provider=CountingProvider(),
            worker_node_provider=_FakeProvider([]),
            business_node_provider=_FakeProvider([]),
            node_catalog_cache=cache,
        )
        first = await uc.execute(ListNodeCatalogCommand())
        second = await uc.execute(ListNodeCatalogCommand())
        assert first.etag == second.etag
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_cache_invalidation_triggers_refetch(self):
        call_count = {"n": 0}

        class CountingProvider:
            def list_summaries(self, tenant_id="system"):
                call_count["n"] += 1
                return [_summary("builtin:a")]

        cache = NodeCatalogCache()
        uc = ListNodeCatalogUseCase(
            logger=DummyLogger(),
            builtin_node_provider=CountingProvider(),
            worker_node_provider=_FakeProvider([]),
            business_node_provider=_FakeProvider([]),
            node_catalog_cache=cache,
        )
        await uc.execute(ListNodeCatalogCommand(tenant_id="t-a"))
        cache.invalidate("t-a")
        await uc.execute(ListNodeCatalogCommand(tenant_id="t-a"))
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_cache_per_tenant_keying(self):
        """Two tenants should not share cache entries."""
        builtin = [_summary("builtin:a")]

        class BusinessFake:
            def list_summaries(self, tenant_id="system"):
                return [_summary(f"business:{tenant_id}")]

        cache = NodeCatalogCache()
        uc = ListNodeCatalogUseCase(
            logger=DummyLogger(),
            builtin_node_provider=_FakeProvider(builtin),
            worker_node_provider=_FakeProvider([]),
            business_node_provider=BusinessFake(),
            node_catalog_cache=cache,
        )
        r_sys = await uc.execute(ListNodeCatalogCommand(tenant_id="system"))
        r_t = await uc.execute(ListNodeCatalogCommand(tenant_id="tenant-a"))
        sys_ids = {s.id for s in r_sys.definitions}
        t_ids = {s.id for s in r_t.definitions}
        assert "business:system" in sys_ids
        assert "business:tenant-a" in t_ids
