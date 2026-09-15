"""T1 — NodeDefinition declares supported credential kinds + provider affinity (F9/F10).

The credential_ref itself is per-instance node config (reserved input key), not a
template field; only the picker-filtering metadata lives on the definition.
"""

import pytest
from app.layer1_domain.entities.node_definition import NodeDefinition


class TestNodeDefinitionCredentials:

    def test_defaults_are_empty(self):
        nd = NodeDefinition(tenant_id="t1", name="N", base_worker_id="w1")
        assert nd.supported_credential_kinds == []
        assert nd.credential_provider is None

    def test_round_trips_kinds_and_provider(self):
        nd = NodeDefinition(
            tenant_id="t1", name="HTTP", base_worker_id="http_request",
            supported_credential_kinds=["API_KEY", "PERSONAL_ACCESS_TOKEN"],
            credential_provider="GENERIC_HTTP",
        )
        assert nd.supported_credential_kinds == ["API_KEY", "PERSONAL_ACCESS_TOKEN"]
        assert nd.credential_provider == "GENERIC_HTTP"

    def test_validate_accepts_known_kinds(self):
        for kind in ("API_KEY", "PERSONAL_ACCESS_TOKEN", "BASIC_AUTH", "JWT",
                     "OAUTH2_DELEGATED", "OAUTH2_APPLICATION", "WEBHOOK_URL",
                     "DATABASE_CONNECTION"):
            nd = NodeDefinition(tenant_id="t1", name="N", base_worker_id="w1",
                                supported_credential_kinds=[kind])
            assert nd.validate() == [], f"{kind} should be valid"

    def test_validate_rejects_unknown_kind(self):
        nd = NodeDefinition(tenant_id="t1", name="N", base_worker_id="w1",
                            supported_credential_kinds=["NOPE"])
        errors = nd.validate()
        assert any("credential" in e.lower() for e in errors)
