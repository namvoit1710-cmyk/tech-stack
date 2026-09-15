import asyncio
from types import SimpleNamespace

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer2_application.features import FEATURE_REGISTRY, load_features
from app.layer2_application.features.backfill_node_taxonomy.use_cases.backfill_node_taxonomy_use_case import (
    BackfillNodeTaxonomyUseCase,
)
from app.layer3_adapters.controllers.restful.v1.node_definition_controller import (
    _get_backfill,
    backfill_taxonomy,
    router as nd_router,
)
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
    MemoryNodeDefinitionRepository,
)


class _Log:
    def info(self, *a, **k):
        pass
    warning = error = debug = info


def _legacy_row(repo, id, tenant_id):
    nd = NodeDefinition(id=id, tenant_id=tenant_id, name=id, base_worker_id="w")
    nd.kind = ""
    nd.collection = ""
    repo.save(nd)
    return nd


def test_backfill_route_registered():
    paths = {(r.path, tuple(sorted(r.methods))) for r in nd_router.routes}
    assert ("/backfill-taxonomy", ("POST",)) in paths


def test_existing_node_definition_routes_present():
    paths = {(r.path, tuple(sorted(r.methods))) for r in nd_router.routes}
    assert ("", ("POST",)) in paths                          # create still there
    assert ("/{node_definition_id}", ("GET",)) in paths      # get still there


def test_backfill_feature_auto_registers():
    load_features()
    assert "backfill_node_taxonomy" in FEATURE_REGISTRY


def test_route_actually_dispatches_through_the_di_container():
    """FIX4 regression guard: the earlier version of this test only checked
    that the route path/method existed in ``router.routes`` and that the
    feature class was discovered by auto-scan — it never actually invoked the
    endpoint. A ``KeyError`` from ``container["backfill_node_taxonomy"]``
    (e.g. a typo in the DI key, or the use case silently skipped at
    container-build time because a required constructor dependency was
    missing) would therefore only ever surface in production. This builds a
    fake ``Request`` the way ``_get_backfill`` expects to find one, resolves
    the dependency through it exactly as FastAPI would, and calls the real
    endpoint function -- also proving the (now required) ``tenant_id`` param
    is tenant-scoped (FIX4): a legacy row belonging to a different tenant must
    not be touched.
    """
    repo = MemoryNodeDefinitionRepository()
    _legacy_row(repo, "a1", tenant_id="tenant-a")
    _legacy_row(repo, "b1", tenant_id="tenant-b")

    usecase = BackfillNodeTaxonomyUseCase(logger=_Log(), node_definition_repo=repo)
    fake_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(container={"backfill_node_taxonomy": usecase}),
        ),
    )

    resolved_usecase = _get_backfill(fake_request)
    response = asyncio.run(
        backfill_taxonomy(tenant_id="tenant-a", usecase=resolved_usecase)
    )

    assert response["status"] == "ok"
    assert response["data"]["backfilled"] == 1
    assert repo.get_by_id("a1").kind == "action"
    # Tenant B's row is untouched by a tenant-A backfill run.
    untouched = repo.get_by_id("b1")
    assert untouched.kind == ""
    assert untouched.collection == ""
