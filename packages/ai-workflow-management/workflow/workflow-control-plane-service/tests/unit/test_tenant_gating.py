"""Tests for the Phase 3 per-tenant v2 rollout gate."""

import pytest

from app.layer3_adapters.controllers.restful.v1.tenant_gating import (
    reset_allowlist_cache,
    tenant_is_gated_out,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_allowlist_cache()
    yield
    reset_allowlist_cache()


class TestEmptyAllowlist:
    def test_empty_string_allows_everyone(self):
        assert tenant_is_gated_out("anyone", "") is False

    def test_whitespace_only_allows_everyone(self):
        assert tenant_is_gated_out("anyone", "   ") is False


class TestNonEmptyAllowlist:
    def test_tenant_on_list_allowed(self):
        assert tenant_is_gated_out("acme", "acme,beta") is False

    def test_tenant_off_list_gated(self):
        assert tenant_is_gated_out("gamma", "acme,beta") is True

    def test_whitespace_tolerant(self):
        assert tenant_is_gated_out("acme", " acme , beta ") is False
        assert tenant_is_gated_out("gamma", " acme , beta ") is True

    def test_case_sensitive(self):
        """Tenant IDs are case-sensitive identifiers — 'ACME' ≠ 'acme'."""
        assert tenant_is_gated_out("ACME", "acme") is True

    def test_empty_tenant_id_gated_when_allowlist_nonempty(self):
        """A blank tenant_id should never pass an allowlist."""
        assert tenant_is_gated_out("", "acme") is True
        assert tenant_is_gated_out("   ", "acme") is True
