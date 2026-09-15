"""Tests for BusinessNodeProvider — tenant-authored NodeDefinition records."""

import pytest

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.providers import BusinessNodeProvider
from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodeRuntime,
)


def _nd(id_: str, tenant_id: str = "system", **overrides) -> NodeDefinition:
    base = dict(
        id=id_,
        tenant_id=tenant_id,
        name=f"Def {id_}",
        description="",
        base_type=NodeType.TASK,
        base_worker_id="some-worker",
        spec_version="1.0.0",
    )
    base.update(overrides)
    return NodeDefinition(**base)


@pytest.fixture
def provider(node_definition_repo) -> BusinessNodeProvider:
    return BusinessNodeProvider(
        repo=node_definition_repo,
        resolver=NodeDefinitionResolver(),
    )


class TestListSummaries:
    def test_empty_repo_returns_empty_list(self, provider):
        assert provider.list_summaries() == []

    def test_system_definitions_visible_to_all_tenants(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("sys-1", tenant_id="system"))
        assert len(provider.list_summaries("system")) == 1
        assert len(provider.list_summaries("acme-tenant")) == 1

    def test_tenant_only_not_visible_to_system(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("sys-1", tenant_id="system"))
        node_definition_repo.save(_nd("t-1", tenant_id="acme-tenant"))
        sys_view = provider.list_summaries("system")
        tenant_view = provider.list_summaries("acme-tenant")
        assert {s.id for s in sys_view} == {"business:sys-1"}
        assert {s.id for s in tenant_view} == {"business:sys-1", "business:t-1"}

    def test_tenant_overrides_system_on_id_collision(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("shared", tenant_id="system", name="System version"))
        node_definition_repo.save(_nd("shared", tenant_id="acme-tenant", name="Tenant override"))
        summaries = provider.list_summaries("acme-tenant")
        shared = next(s for s in summaries if s.id == "business:shared")
        assert shared.name == "Tenant override"

    def test_summary_carries_spec_version(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("v", tenant_id="system", spec_version="2.3.4"))
        s = provider.list_summaries()[0]
        assert s.spec_version == "2.3.4"

    def test_task_base_type_maps_to_worker_runtime(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("t", base_type=NodeType.TASK))
        s = provider.list_summaries()[0]
        assert s.runtime == NodeRuntime.WORKER

    def test_input_base_type_maps_to_pending_runtime(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("t", base_type=NodeType.INPUT, base_worker_id=None))
        s = provider.list_summaries()[0]
        assert s.runtime == NodeRuntime.PENDING

    def test_condition_base_type_maps_to_engine_runtime(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("t", base_type=NodeType.CONDITION, base_worker_id=None))
        s = provider.list_summaries()[0]
        assert s.runtime == NodeRuntime.ENGINE


class TestPortSerialisation:
    """Ports must be serialised for the editor catalog, falling back to the
    base-type spec when a definition declares none (e.g. gateway-generated
    nodes persist ``ports={}``). Mirrors the runtime spec resolver so the
    editor and engine agree on a node's handles.
    """

    def test_empty_ports_falls_back_to_task_base_ports(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("gw", base_type=NodeType.TASK, ports={}))
        s = provider.list_summaries()[0]
        assert [p.id for p in s.ports.inputs] == ["in_default"]
        assert [p.id for p in s.ports.outputs] == ["success", "failure"]

    def test_empty_ports_falls_back_to_condition_base_ports(self, provider, node_definition_repo):
        node_definition_repo.save(
            _nd("c", base_type=NodeType.CONDITION, base_worker_id=None, ports={})
        )
        s = provider.list_summaries()[0]
        assert [p.id for p in s.ports.inputs] == ["in_default"]
        assert [p.id for p in s.ports.outputs] == ["true_branch", "false_branch"]

    def test_explicit_ports_pass_through_unchanged(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("e", ports={
            "in": [{"id": "in_a", "label": "A"}],
            "out": [{"id": "out_x", "label": "X"}, {"id": "out_y", "label": "Y"}],
        }))
        s = provider.list_summaries()[0]
        assert [(p.id, p.label) for p in s.ports.inputs] == [("in_a", "A")]
        assert [p.id for p in s.ports.outputs] == ["out_x", "out_y"]

    def test_detail_also_carries_fallback_ports(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("gw", base_type=NodeType.TASK, ports={}))
        d = provider.get_detail("business:gw")
        assert [p.id for p in d.ports.inputs] == ["in_default"]
        assert [p.id for p in d.ports.outputs] == ["success", "failure"]


class TestGetDetail:
    def test_returns_detail_for_known_definition(self, provider, node_definition_repo):
        node_definition_repo.save(_nd(
            "abc",
            tenant_id="system",
            locked_defaults={"url": "https://mail"},
            input_mapping={"body.to": "{{$input.to}}"},
            spec_version="1.0.0",
        ))
        d = provider.get_detail("business:abc")
        assert isinstance(d, NodeDefinitionDetail)
        assert d.locked_defaults == {"url": "https://mail"}
        assert d.input_mapping == {"body.to": "{{$input.to}}"}
        assert d.base_worker_id == "some-worker"

    def test_returns_none_for_unknown(self, provider):
        assert provider.get_detail("business:missing") is None

    def test_returns_none_for_non_business_prefix(self, provider):
        assert provider.get_detail("builtin:condition") is None
        assert provider.get_detail("worker:http") is None

    def test_empty_id_returns_none(self, provider):
        assert provider.get_detail("business:") is None

    def test_pinned_version_match_succeeds(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("v", spec_version="3.0.0"))
        d = provider.get_detail("business:v", spec_version="3.0.0")
        assert d is not None

    def test_pinned_version_mismatch_raises(self, provider, node_definition_repo):
        node_definition_repo.save(_nd("v", spec_version="1.0.0"))
        with pytest.raises(ValueError, match="spec_version"):
            provider.get_detail("business:v", spec_version="2.0.0")


class TestInheritanceIntegration:
    def test_child_inherits_parent_base_worker_id(self, provider, node_definition_repo):
        parent = _nd("p", base_worker_id="parent-worker")
        child = _nd("c", parent_definition_id="p", base_worker_id=None)
        node_definition_repo.save(parent)
        node_definition_repo.save(child)
        d = provider.get_detail("business:c")
        assert d.base_worker_id == "parent-worker"

    def test_child_inherits_parent_locked_defaults_merged(self, provider, node_definition_repo):
        parent = _nd("p", locked_defaults={"a": 1, "b": 2})
        child = _nd("c", parent_definition_id="p", locked_defaults={"b": 99, "c": 3})
        node_definition_repo.save(parent)
        node_definition_repo.save(child)
        d = provider.get_detail("business:c")
        assert d.locked_defaults == {"a": 1, "b": 99, "c": 3}
