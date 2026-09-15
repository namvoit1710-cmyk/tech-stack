import asyncio

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
    MemoryNodeDefinitionRepository,
)
from app.layer2_application.features.backfill_node_taxonomy.use_cases.backfill_node_taxonomy_use_case import (
    BackfillNodeTaxonomyUseCase,
)


class _Log:
    def info(self, *a, **k):
        pass
    warning = error = debug = info


def _legacy_row(repo, id, tenant_id="t1", **kw):
    """A row as it existed before the KIND/COLLECTION columns: blank taxonomy."""
    nd = NodeDefinition(id=id, tenant_id=tenant_id, name=id, base_worker_id="w", **kw)
    nd.kind = ""          # simulate pre-column NULL (set post-init, bypassing heal)
    nd.collection = ""
    repo.save(nd)
    return nd


def test_backfills_missing_kind_and_collection():
    repo = MemoryNodeDefinitionRepository()
    _legacy_row(repo, "x", tenant_id="t1", locked_defaults={"method": "GET"})   # GET -> read
    uc = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)

    result = asyncio.run(uc.execute(tenant_id="t1"))

    assert result.backfilled == 1
    healed = repo.get_by_id("x")
    assert healed.kind == "read"
    assert healed.collection == "My Nodes"


def test_is_idempotent():
    repo = MemoryNodeDefinitionRepository()
    _legacy_row(repo, "x", tenant_id="t1", locked_defaults={"method": "POST"})
    uc = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)
    asyncio.run(uc.execute(tenant_id="t1"))
    second = asyncio.run(uc.execute(tenant_id="t1"))
    assert second.backfilled == 0


def test_does_not_clobber_explicit_values():
    repo = MemoryNodeDefinitionRepository()
    nd = NodeDefinition(id="y", tenant_id="t1", name="Explicit", base_worker_id="w",
                        kind="util", collection="Finance", locked_defaults={"method": "GET"})
    repo.save(nd)   # already has taxonomy -> not in the missing set
    uc = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)

    result = asyncio.run(uc.execute(tenant_id="t1"))

    assert result.backfilled == 0
    kept = repo.get_by_id("y")
    assert kept.kind == "util"
    assert kept.collection == "Finance"


def test_does_not_touch_other_tenants_rows():
    """FIX4 (SECURITY): the backfill used to run unscoped across every tenant
    with no LIMIT. A definition belonging to tenant B must not be touched when
    the backfill runs for tenant A."""
    repo = MemoryNodeDefinitionRepository()
    _legacy_row(repo, "a1", tenant_id="tenant-a", locked_defaults={"method": "GET"})
    _legacy_row(repo, "b1", tenant_id="tenant-b", locked_defaults={"method": "GET"})
    uc = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)

    result = asyncio.run(uc.execute(tenant_id="tenant-a"))

    assert result.backfilled == 1
    assert repo.get_by_id("a1").kind == "read"
    # Tenant B's row is untouched -- still blank, not silently rewritten.
    untouched = repo.get_by_id("b1")
    assert untouched.kind == ""
    assert untouched.collection == ""


def test_pages_through_a_backlog_larger_than_one_page():
    """FIX4: a legacy backlog bigger than the page size must be fully drained
    across multiple ``list_missing_taxonomy`` calls, not truncated to one page."""
    repo = MemoryNodeDefinitionRepository()
    for i in range(5):
        _legacy_row(repo, f"row-{i}", tenant_id="t1", locked_defaults={"method": "POST"})
    uc = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)

    result = asyncio.run(uc.execute(tenant_id="t1", limit=2))

    assert result.backfilled == 5
    for i in range(5):
        assert repo.get_by_id(f"row-{i}").kind == "action"
