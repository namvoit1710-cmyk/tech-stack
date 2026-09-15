"""Tests for NodeDefinition entity construction and validation."""

import pytest
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType


class TestNodeDefinitionEntity:

    def test_defaults(self):
        nd = NodeDefinition(tenant_id="t1", name="My Node", base_worker_id="w1")
        assert nd.tenant_id == "t1"
        assert nd.name == "My Node"
        assert nd.base_type == NodeType.TASK
        assert nd.base_worker_id == "w1"
        assert nd.locked_defaults == {}
        assert nd.input_schema == []
        assert nd.output_schema == []
        assert nd.input_mapping == {}
        assert nd.output_mapping == {}
        assert nd.icon == ""
        assert nd.color == ""
        assert nd.tags == []
        assert nd.data_mode == "json"
        assert nd.edit_mode == "none"
        assert nd.id  # auto-generated
        assert nd.created_at
        assert nd.updated_at

    def test_base_type_string_coercion(self):
        nd = NodeDefinition(tenant_id="t1", name="Cond", base_type="CONDITION")
        assert nd.base_type == NodeType.CONDITION

    def test_base_worker_id_prefix_normalized_to_bare(self):
        # The catalog exposes a worker as the id "worker:<type>"; a definition
        # stored with that catalog id is normalized to the bare worker_type.
        nd = NodeDefinition(tenant_id="t1", name="N", base_worker_id="worker:http_request")
        assert nd.base_worker_id == "http_request"

    def test_base_worker_id_bare_and_none_unchanged(self):
        assert NodeDefinition(
            tenant_id="t1", name="N", base_worker_id="http_request",
        ).base_worker_id == "http_request"
        assert NodeDefinition(
            tenant_id="t1", name="N", base_type="CONDITION", base_worker_id=None,
        ).base_worker_id is None

    def test_spec_version_default(self):
        nd = NodeDefinition(tenant_id="t1", name="A", base_worker_id="w1")
        assert nd.spec_version == "1.0.0"

    def test_spec_version_explicit(self):
        nd = NodeDefinition(
            tenant_id="t1", name="A", base_worker_id="w1",
            spec_version="2.3.1",
        )
        assert nd.spec_version == "2.3.1"

    def test_validate_happy(self):
        nd = NodeDefinition(tenant_id="t1", name="OK", base_worker_id="w1")
        assert nd.validate() == []

    def test_validate_missing_name(self):
        nd = NodeDefinition(tenant_id="t1", name="", base_worker_id="w1")
        errors = nd.validate()
        assert any("name" in e for e in errors)

    def test_validate_missing_tenant(self):
        nd = NodeDefinition(tenant_id="", name="OK", base_worker_id="w1")
        errors = nd.validate()
        assert any("tenant_id" in e for e in errors)

    def test_validate_task_requires_worker_id(self):
        nd = NodeDefinition(tenant_id="t1", name="OK", base_type=NodeType.TASK, base_worker_id=None)
        errors = nd.validate()
        assert any("base_worker_id" in e for e in errors)

    def test_validate_non_task_no_worker_required(self):
        nd = NodeDefinition(tenant_id="t1", name="OK", base_type=NodeType.CONDITION, base_worker_id=None)
        assert nd.validate() == []

    def test_validate_invalid_data_mode(self):
        nd = NodeDefinition(tenant_id="t1", name="OK", base_worker_id="w1", data_mode="invalid")
        errors = nd.validate()
        assert any("data_mode" in e for e in errors)

    def test_validate_invalid_edit_mode(self):
        nd = NodeDefinition(tenant_id="t1", name="OK", base_worker_id="w1", edit_mode="invalid")
        errors = nd.validate()
        assert any("edit_mode" in e for e in errors)

    def test_validate_csv_modes(self):
        for mode in ("json", "csv_single", "csv_multi"):
            nd = NodeDefinition(tenant_id="t1", name="OK", base_worker_id="w1", data_mode=mode)
            assert nd.validate() == []

    def test_validate_edit_modes(self):
        for mode in ("none", "before_dispatch", "after_dispatch"):
            nd = NodeDefinition(tenant_id="t1", name="OK", base_worker_id="w1", edit_mode=mode)
            assert nd.validate() == []

    def test_full_construction(self):
        nd = NodeDefinition(
            tenant_id="t1",
            name="Get Orders",
            description="Fetch customer orders",
            base_type=NodeType.TASK,
            base_worker_id="http-request-worker",
            locked_defaults={"method": "GET", "url": "https://api/orders"},
            input_schema=[{"key": "customer_id", "type": "string"}],
            output_schema=[{"key": "orders", "type": "array"}],
            input_mapping={"query_params": "{{custom.customer_id}}"},
            output_mapping={"orders": "{{_output.body.items}}"},
            icon="ShoppingCart",
            color="#FF5722",
            tags=["orders", "crm"],
        )
        assert nd.locked_defaults["method"] == "GET"
        assert len(nd.input_schema) == 1
        assert nd.tags == ["orders", "crm"]
