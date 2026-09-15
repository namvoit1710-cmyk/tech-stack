"""Integration tests for /api/v2/node-catalog (Phase 1).

Covers:
- flag-off: v2 endpoints 404 when ``ENABLE_NODE_CATALOG_V2=false``.
- flag-on: list + detail + batch return expected shapes.
- ETag round-trip (200 → If-None-Match → 304).
- 404 on unknown ref.
- 400 on over-50 batch refs.
- ``@<version>`` parsing in the detail ref path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container


def _client_with_flag(
    enabled: bool,
    *,
    strict_contracts: bool = False,
    tenant_allowlist: str = "",
) -> TestClient:
    """Build an app with ENABLE_NODE_CATALOG_V2 toggled.

    Settings is module-singleton; mutate it directly for the duration
    of the test. A fresh FastAPI app is built each time to pick up the
    current flag values.
    """
    from app.layer4_frameworks.config.app_config import settings
    from app.layer3_adapters.controllers.restful.v1.tenant_gating import (
        reset_allowlist_cache,
    )
    settings.ENABLE_NODE_CATALOG_V2 = enabled
    settings.STRICT_CONTRACT_VALIDATION = strict_contracts
    settings.NODE_CATALOG_V2_TENANT_ALLOWLIST = tenant_allowlist
    reset_allowlist_cache()
    container = build_app_container()
    app = create_fastapi_app(container)
    return TestClient(app)


@pytest.fixture
def client_v2_on() -> TestClient:
    return _client_with_flag(True)


@pytest.fixture
def client_v2_off() -> TestClient:
    return _client_with_flag(False)


class TestFeatureFlag:
    def test_endpoints_404_when_flag_off(self, client_v2_off):
        assert client_v2_off.get("/api/v2/node-catalog").status_code == 404
        assert client_v2_off.get("/api/v2/node-catalog/builtin:condition").status_code == 404
        assert client_v2_off.post(
            "/api/v2/node-catalog/details",
            json={"refs": ["builtin:condition"]},
        ).status_code == 404

    def test_legacy_palette_unaffected_when_flag_off(self, client_v2_off):
        r = client_v2_off.get("/api/v1/node-palette")
        assert r.status_code == 200

    def test_legacy_palette_no_deprecation_header_when_flag_off(self, client_v2_off):
        r = client_v2_off.get("/api/v1/node-palette")
        assert "deprecation" not in {k.lower() for k in r.headers.keys()}
        assert "sunset" not in {k.lower() for k in r.headers.keys()}


class TestLegacyDeprecation:
    def test_legacy_palette_carries_deprecation_header_when_flag_on(self, client_v2_on):
        r = client_v2_on.get("/api/v1/node-palette")
        assert r.status_code == 200
        assert r.headers.get("deprecation") == "true"
        assert r.headers.get("sunset")  # Sunset date string present
        # Link header points clients at the new endpoint.
        assert "/api/v2/node-catalog" in r.headers.get("link", "")
        assert 'rel="successor-version"' in r.headers.get("link", "")


class TestListEndpoint:
    def test_returns_200_with_envelope(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "data" in body

    def test_body_shape(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog")
        data = r.json()["data"]
        assert "definitions" in data
        assert "category_tree" in data
        assert "categories" in data
        assert "generated_at" in data
        assert "total" in data
        assert data["total"] == len(data["definitions"])

    def test_categories_has_labels_for_every_tree_key(self, client_v2_on):
        """Every key in ``category_tree`` must have an entry in ``categories``
        so the frontend can render the palette group header without guessing.
        Parent paths (e.g. ``control_flow``) are included too."""
        data = client_v2_on.get("/api/v2/node-catalog").json()["data"]
        tree_keys = set(data["category_tree"].keys())
        cats = data["categories"]
        assert tree_keys.issubset(set(cats.keys()))
        # Post-SA-1734 keys: the "Core" collection sorts first, and a
        # <collection>.<kind> leaf carries the fixed kind label.
        assert cats["Core"]["label"] == "Core"
        assert cats["Core"]["order"] == 0
        assert cats["Core.logic"]["label"] == "Logic & Flow"

    def test_includes_builtins(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog")
        definitions = r.json()["data"]["definitions"]
        ids = {d["id"] for d in definitions}
        # 18 builtin catalog types: CONDITION, SWITCH, LOOP, PARALLEL,
        # MERGE, TRIGGER, WORKFLOW, COMPUTE, INPUT, HUMAN_ACTION,
        # DATA_EDIT, TEMPLATE_DATA_EDIT, FILE_UPLOAD, OUTPUT, SET_VARIABLE,
        # APPROVAL_FLOW, PROMPT, WAIT.
        # TASK and LOOP_EXIT/LOOP_CONTINUE are spec'd but unlisted.
        # Worker/business may or may not be present depending on fixtures.
        assert "builtin:condition" in ids
        assert "builtin:input" in ids
        assert "builtin:compute" in ids
        assert "builtin:set_variable" in ids
        assert "builtin:prompt" in ids
        assert "builtin:template_data_edit" in ids
        assert len([i for i in ids if i.startswith("builtin:")]) == 18

    def test_etag_header_present(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog")
        etag = r.headers.get("etag", "")
        assert etag.startswith('W/"')

    def test_etag_round_trip_returns_304(self, client_v2_on):
        r1 = client_v2_on.get("/api/v2/node-catalog")
        etag = r1.headers["etag"]
        r2 = client_v2_on.get(
            "/api/v2/node-catalog",
            headers={"If-None-Match": etag},
        )
        assert r2.status_code == 304
        assert r2.headers.get("etag") == etag
        assert r2.content == b""


class TestDetailEndpoint:
    def test_merge_detail_serves_canonical_vocabulary(self, client_v2_on):
        """FE-contract canary (NodeSpec redesign): MERGE's strategy select
        must offer the canonical handler vocabulary all|any|n_of_m and the
        newly-declared on_branch_failure field. If this fails, the FE
        coordination doc (docs/design/node-spec-fe-changes.md) is stale."""
        r = client_v2_on.get("/api/v2/node-catalog/builtin:merge")
        assert r.status_code == 200
        data = r.json()["data"]
        fields = {f["key"]: f for f in data["input_schema"]}
        options = fields["merge_strategy"]["field_config"]["control_props"]["options"]
        assert [o["id"] for o in options] == ["all", "any", "n_of_m"]
        assert "on_branch_failure" in fields
        # PARALLEL's dead fields stay gone (empty config form).
        r2 = client_v2_on.get("/api/v2/node-catalog/builtin:parallel")
        assert r2.status_code == 200
        assert r2.json()["data"]["input_schema"] == []

    def test_merge_exposes_in_default_input_port(self, client_v2_on):
        """SA-2176: MERGE must declare a generic ``in_default`` input handle
        (mirror of PARALLEL) so a freshly-dropped Merge node has an input to
        wire the first edge into. Regression guard for the empty ``in_ports=()``."""
        r = client_v2_on.get("/api/v2/node-catalog/builtin:merge")
        assert r.status_code == 200
        ports = r.json()["data"]["ports"]
        assert "in_default" in {p["id"] for p in ports["inputs"]}
        assert "out_default" in {p["id"] for p in ports["outputs"]}

    def test_builtin_detail_returns_200(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog/builtin:condition")
        assert r.status_code == 200

    def test_detail_contains_schemas(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog/builtin:condition")
        data = r.json()["data"]
        assert data["id"] == "builtin:condition"
        assert data["provider"] == "builtin"
        assert "input_schema" in data
        assert "output_schema" in data
        assert len(data["input_schema"]) > 0

    def test_unknown_ref_returns_404(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog/builtin:nonexistent")
        assert r.status_code == 404
        body = r.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "NOT_FOUND"

    def test_malformed_ref_returns_400(self, client_v2_on):
        """Two ``@`` characters is not a valid ref."""
        r = client_v2_on.get("/api/v2/node-catalog/builtin:condition@1.0.0@extra")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_REF"

    def test_version_suffix_accepted(self, client_v2_on):
        r = client_v2_on.get("/api/v2/node-catalog/builtin:condition@1.0.0")
        assert r.status_code == 200

    def test_wrong_version_returns_422(self, client_v2_on):
        """Builtins only have ``1.0.0``; asking for a different version
        is a client error surfaced as 422 VERSION_MISMATCH."""
        r = client_v2_on.get("/api/v2/node-catalog/builtin:condition@9.9.9")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VERSION_MISMATCH"

    def test_detail_etag_round_trip(self, client_v2_on):
        url = "/api/v2/node-catalog/builtin:compute"
        r1 = client_v2_on.get(url)
        etag = r1.headers["etag"]
        r2 = client_v2_on.get(url, headers={"If-None-Match": etag})
        assert r2.status_code == 304


class TestTenantAllowlist:
    """Phase 3 gradual rollout — per-tenant allowlist gating."""

    def test_allowlisted_tenant_reaches_v2(self):
        client = _client_with_flag(True, tenant_allowlist="acme,beta")
        r = client.get("/api/v2/node-catalog?tenant_id=acme")
        assert r.status_code == 200

    def test_non_allowlisted_tenant_gets_404(self):
        client = _client_with_flag(True, tenant_allowlist="acme,beta")
        r = client.get("/api/v2/node-catalog?tenant_id=gamma")
        assert r.status_code == 404

    def test_empty_allowlist_allows_everyone(self):
        client = _client_with_flag(True, tenant_allowlist="")
        assert client.get("/api/v2/node-catalog?tenant_id=anyone").status_code == 200
        assert client.get("/api/v2/node-catalog?tenant_id=system").status_code == 200

    def test_allowlist_gates_detail_endpoint(self):
        client = _client_with_flag(True, tenant_allowlist="acme")
        # Note: tenant_id is a query param on the detail endpoint too.
        assert client.get(
            "/api/v2/node-catalog/builtin:condition?tenant_id=gamma",
        ).status_code == 404
        assert client.get(
            "/api/v2/node-catalog/builtin:condition?tenant_id=acme",
        ).status_code == 200

    def test_allowlist_gates_batch_endpoint(self):
        client = _client_with_flag(True, tenant_allowlist="acme")
        r = client.post("/api/v2/node-catalog/details", json={
            "refs": ["builtin:condition"],
            "tenant_id": "gamma",
        })
        assert r.status_code == 404

        r = client.post("/api/v2/node-catalog/details", json={
            "refs": ["builtin:condition"],
            "tenant_id": "acme",
        })
        assert r.status_code == 200


class TestStrictContractValidation:
    """Wires ``STRICT_CONTRACT_VALIDATION`` from settings into the providers.

    Business-node provider is the easiest vector to exercise end-to-end:
    seed a NodeDefinition with a malformed FormField and observe whether
    the detail endpoint 200s (lenient, bad field dropped) or 500s (strict,
    ValidationError bubbles).
    """

    def _seed_malformed_business_node(self, container) -> str:
        from app.layer1_domain.entities.node_definition import NodeDefinition
        from app.layer1_domain.value_objects.node_type import NodeType
        nd = NodeDefinition(
            id="biz-bad-1",
            tenant_id="system",
            name="Bad definition",
            base_type=NodeType.TASK,
            base_worker_id="http-worker",
            spec_version="1.0.0",
            input_schema=[
                # Malformed: SelectControl with no options and no dynamic_source.
                {
                    "key": "choice",
                    "outputType": "string",
                    "fieldConfig": {"fieldControl": "SelectControl"},
                },
            ],
        )
        container["_infra"]  # ensure bootstrap finished
        # Access the repo via the business provider's injected repo.
        # It's held inside the provider; easier is to grab from infra repos.
        from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
            MemoryNodeDefinitionRepository,
        )
        # Each bootstrap call constructs a fresh memory repo; we just
        # reach in through the provider.
        provider = container["list_node_catalog"]._business
        provider._repo.save(nd)
        return nd.id

    def test_lenient_drops_bad_field(self):
        client = _client_with_flag(True, strict_contracts=False)
        container = client.app.state.container
        self._seed_malformed_business_node(container)

        r = client.get("/api/v2/node-catalog/business:biz-bad-1")
        assert r.status_code == 200
        data = r.json()["data"]
        # Bad field dropped; no good fields to keep → empty list.
        assert data["input_schema"] == []

    def test_strict_mode_rejects_bad_field_with_422(self):
        """Strict mode lets pydantic's ValidationError (a ValueError subclass)
        propagate. The detail controller's ValueError handler maps it to 422
        — same code as the version-mismatch path, semantically appropriate
        for "shape is valid but content can't be processed"."""
        client = _client_with_flag(True, strict_contracts=True)
        container = client.app.state.container
        self._seed_malformed_business_node(container)

        r = client.get("/api/v2/node-catalog/business:biz-bad-1")
        assert r.status_code == 422


class TestBatchEndpoint:
    def test_batch_returns_details_and_missing(self, client_v2_on):
        r = client_v2_on.post("/api/v2/node-catalog/details", json={
            "refs": ["builtin:condition", "builtin:switch", "builtin:nope"],
            "tenant_id": "system",
        })
        assert r.status_code == 200
        data = r.json()["data"]
        found_ids = {d["id"] for d in data["details"]}
        missing_ids = {m["node_id"] for m in data["missing"]}
        assert found_ids == {"builtin:condition", "builtin:switch"}
        assert missing_ids == {"builtin:nope"}

    def test_batch_rejects_over_50_refs(self, client_v2_on):
        """Pydantic Field(max_length=50) catches this at DTO validation → 422."""
        refs = [f"builtin:condition" for _ in range(51)]
        r = client_v2_on.post("/api/v2/node-catalog/details", json={"refs": refs})
        assert r.status_code == 422

    def test_batch_rejects_empty_refs(self, client_v2_on):
        r = client_v2_on.post("/api/v2/node-catalog/details", json={"refs": []})
        assert r.status_code == 422

    def test_batch_rejects_malformed_ref(self, client_v2_on):
        r = client_v2_on.post("/api/v2/node-catalog/details", json={
            "refs": ["bad@ref@with@too@many@ats"],
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_REF"
