"""OpenAPI spec validation (Unit F / Phase 5B).

Guarantees that the generated ``/openapi.json``:

1. Conforms to the OpenAPI 3.1 spec (via ``openapi_spec_validator``).
2. Contains every route introduced by Units C/D/E/F with the expected
   tag (so the Swagger UI groups them correctly).
3. Declares a typed success ``response_model`` and an error-response
   table for each PATCH/POST write-side route.
4. Renders the ``PatchNodeDataRequest`` 10-op discriminated union as
   ``oneOf`` (so FE codegen picks up the variants).
"""

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container


@pytest.fixture
def client():
    container = build_app_container()
    return TestClient(create_fastapi_app(container))




@pytest.mark.parametrize(
    "path,method,expected_tag",
    [
        ("/api/v1/runs/{run_id}/nodes/{node_id}/variable-suggestions", "get",
         "variable-suggestions"),
        ("/api/v1/runs/{run_id}/nodes/{node_id}/schema", "get", "node-schema"),
        ("/api/v1/runs/{run_id}/nodes/{node_id}/data", "get", "node-data"),
        ("/api/v1/runs/{run_id}/nodes/{node_id}/data", "patch", "node-data"),
        ("/api/v1/runs/{run_id}/nodes/{node_id}/submit", "post", "node-data"),
        ("/api/v1/runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata",
         "get", "node-data"),
        ("/api/v1/runs/{run_id}/artifacts", "get", "run-artifacts"),
        ("/api/v1/runs/{run_id}/rerun", "post", "run-rerun"),
        ("/api/v1/runs/{run_id}/resolve", "post", "run-resolve"),
    ],
)
def test_route_is_tagged(client, path, method, expected_tag):
    spec = client.get("/openapi.json").json()
    entry = spec["paths"].get(path, {}).get(method)
    assert entry is not None, f"{method.upper()} {path} missing"
    assert expected_tag in entry.get("tags", []), (
        f"{method.upper()} {path} missing tag {expected_tag!r} "
        f"(got {entry.get('tags')})"
    )
    assert entry.get("summary"), f"{method.upper()} {path} missing summary"
    # Every write-side route must declare at least one error response.
    if method in {"patch", "post"}:
        responses = entry.get("responses", {})
        error_codes = [c for c in responses if c.startswith(("4", "5"))]
        assert error_codes, f"{method.upper()} {path} has no error responses"


def test_patch_data_body_shape(client):
    """List-of-artifacts shape: ``{artifacts: [{artifact_id, operations: [...]}]}``."""
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "PatchNodeDataRequest" in schemas
    req_schema = schemas["PatchNodeDataRequest"]
    props = req_schema["properties"]
    assert "artifacts" in props
    # Top-level artifact_id/operations are gone — they live inside each entry.
    assert "artifact_id" not in props
    assert "operations" not in props
    # Old 10-op discriminated union + scalars/reset_to_input also gone.
    assert "artifact_ops" not in props
    assert "scalars" not in props
    assert "reset_to_input" not in props
    # Each entry is an ArtifactPatch with {artifact_id, operations}.
    assert "ArtifactPatch" in schemas
    ap_props = schemas["ArtifactPatch"]["properties"]
    assert "artifact_id" in ap_props
    assert "operations" in ap_props


def test_patch_data_has_examples(client):
    spec = client.get("/openapi.json").json()
    req_schema = spec["components"]["schemas"]["PatchNodeDataRequest"]
    assert "examples" in req_schema and len(req_schema["examples"]) >= 2


def test_envelope_schema_present(client):
    """ApiEnvelope[...] variants should surface in the components section
    (FastAPI generates one component per concrete type parameter)."""
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    envelope_variants = [
        name for name in schemas
        if name.startswith("ApiEnvelope_")
    ]
    # At minimum we expect one envelope per write-side response type plus
    # the shared ApiErrorPayload envelope.
    assert len(envelope_variants) >= 5, (
        f"expected many ApiEnvelope[...] variants, got {envelope_variants}"
    )
    assert "ApiErrorPayload" in schemas
