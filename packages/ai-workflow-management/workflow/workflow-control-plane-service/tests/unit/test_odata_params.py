"""Tests for the OData parameter parser."""

import pytest

from app.layer3_adapters.controllers.restful.v1.dtos.odata_params import (
    parse_odata_filter,
    ODataFilterResult,
)


class TestParseOdataFilter:

    def test_empty_string(self):
        result = parse_odata_filter("")
        assert result.eq == {}
        assert result.contains == {}

    def test_eq_only(self):
        result = parse_odata_filter("status eq 'ACTIVE'")
        assert result.eq == {"status": "ACTIVE"}
        assert result.contains == {}

    def test_multiple_eq(self):
        result = parse_odata_filter("status eq 'ACTIVE' and tenant_id eq 'system'")
        assert result.eq == {"status": "ACTIVE", "tenant_id": "system"}
        assert result.contains == {}

    def test_contains_only(self):
        result = parse_odata_filter("contains(name, 'approval')")
        assert result.eq == {}
        assert result.contains == {"name": "approval"}

    def test_multiple_contains(self):
        result = parse_odata_filter("contains(name, 'approval') and contains(description, 'invoice')")
        assert result.eq == {}
        assert result.contains == {"name": "approval", "description": "invoice"}

    def test_mixed_eq_and_contains(self):
        result = parse_odata_filter("status eq 'ACTIVE' and contains(name, 'approval')")
        assert result.eq == {"status": "ACTIVE"}
        assert result.contains == {"name": "approval"}

    def test_contains_before_eq(self):
        result = parse_odata_filter("contains(name, 'test') and status eq 'DRAFT'")
        assert result.eq == {"status": "DRAFT"}
        assert result.contains == {"name": "test"}

    def test_contains_with_spaces_in_value(self):
        result = parse_odata_filter("contains(name, 'my workflow')")
        assert result.contains == {"name": "my workflow"}

    def test_contains_empty_value(self):
        result = parse_odata_filter("contains(name, '')")
        assert result.contains == {"name": ""}

    def test_complex_mixed(self):
        result = parse_odata_filter(
            "status eq 'ACTIVE' and contains(name, 'approval') and tenant_id eq 'acme' and contains(description, 'test')"
        )
        assert result.eq == {"status": "ACTIVE", "tenant_id": "acme"}
        assert result.contains == {"name": "approval", "description": "test"}

    def test_returns_odata_filter_result(self):
        result = parse_odata_filter("")
        assert isinstance(result, ODataFilterResult)

    def test_ne_operator(self):
        result = parse_odata_filter("status ne 'DRAFT'")
        assert result.ne == {"status": "DRAFT"}
        assert result.eq == {}

    def test_comparison_operators(self):
        result = parse_odata_filter(
            "created_at gt '2026-01-01' and created_at le '2026-12-31'"
        )
        assert result.gt == {"created_at": "2026-01-01"}
        assert result.le == {"created_at": "2026-12-31"}

    def test_ge_and_lt_operators(self):
        result = parse_odata_filter("version ge '2' and version lt '5'")
        assert result.ge == {"version": "2"}
        assert result.lt == {"version": "5"}

    def test_mixed_all_operators(self):
        result = parse_odata_filter(
            "status eq 'ACTIVE' and version ne '0' "
            "and created_at gt '2026-01-01' and contains(name, 'flow')"
        )
        assert result.eq == {"status": "ACTIVE"}
        assert result.ne == {"version": "0"}
        assert result.gt == {"created_at": "2026-01-01"}
        assert result.contains == {"name": "flow"}
