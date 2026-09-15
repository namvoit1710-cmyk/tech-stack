"""Tests for workflow_version_pinner — spec_version pinning at save time."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer2_application.definition.workflow_version_pinner import pin_spec_versions


class _FakeClient:
    def __init__(self, workers):
        self._workers = workers

    def list_workers(self):
        return list(self._workers)


class _ExplodingClient:
    def list_workers(self):
        raise RuntimeError("registry offline")


class _FakeRepo:
    def __init__(self, store):
        self._store = store

    def get_by_id(self, id):
        return self._store.get(id)


def _node(**overrides):
    """Cheap stand-in for WorkflowNode — only the fields the pinner reads."""
    base = dict(
        id="n",
        spec_version=None,
        node_definition_id=None,
        worker_type=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _workflow(*nodes):
    return SimpleNamespace(nodes=list(nodes))


class TestPreservesAuthorPinning:
    def test_node_with_explicit_spec_version_kept(self):
        n = _node(worker_type="http", spec_version="9.9.9")
        wf = _workflow(n)
        pin_spec_versions(
            wf, resolver=NodeDefinitionResolver(),
            worker_registry_client=_FakeClient([
                {"worker_type": "http", "spec_version": "2.0.0"},
            ]),
        )
        assert n.spec_version == "9.9.9"


class TestBuiltinNodes:
    def test_builtin_worker_type_pins_1_0_0(self):
        n = _node(worker_type="builtin:condition")
        wf = _workflow(n)
        pin_spec_versions(wf, resolver=NodeDefinitionResolver())
        assert n.spec_version == "1.0.0"

    def test_no_worker_or_def_id_pins_1_0_0(self):
        """Nodes with neither a worker nor a definition fall through to 1.0.0."""
        n = _node()  # empty
        wf = _workflow(n)
        pin_spec_versions(wf, resolver=NodeDefinitionResolver())
        assert n.spec_version == "1.0.0"


class TestWorkerNodes:
    def test_worker_pins_to_max_registered_version(self):
        n = _node(worker_type="http")
        wf = _workflow(n)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            worker_registry_client=_FakeClient([
                {"worker_type": "http", "spec_version": "1.0.0"},
                {"worker_type": "http", "spec_version": "2.3.0"},
                {"worker_type": "http", "spec_version": "1.7.0"},
                {"worker_type": "other", "spec_version": "9.9.9"},
            ]),
        )
        assert n.spec_version == "2.3.0"

    def test_worker_with_no_registered_matches_defaults_1_0_0(self):
        n = _node(worker_type="never-registered")
        wf = _workflow(n)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            worker_registry_client=_FakeClient([]),
        )
        assert n.spec_version == "1.0.0"

    def test_client_offline_uses_empty_worker_list(self):
        """If list_workers() raises, pinning must not fail the save."""
        n = _node(worker_type="http")
        wf = _workflow(n)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            worker_registry_client=_ExplodingClient(),
        )
        assert n.spec_version == "1.0.0"


class TestBusinessNodes:
    def test_business_node_pins_to_leaf_spec_version(self):
        from app.layer1_domain.entities.node_definition import NodeDefinition
        from app.layer1_domain.value_objects.node_type import NodeType
        nd = NodeDefinition(
            id="abc", tenant_id="system", name="x",
            base_type=NodeType.TASK, base_worker_id="http",
            spec_version="3.4.5",
        )
        n = _node(node_definition_id="abc")
        wf = _workflow(n)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            node_definition_repo=_FakeRepo({"abc": nd}),
        )
        assert n.spec_version == "3.4.5"

    def test_missing_definition_falls_back(self):
        n = _node(node_definition_id="missing", worker_type="http")
        wf = _workflow(n)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            node_definition_repo=_FakeRepo({}),
            worker_registry_client=_FakeClient([
                {"worker_type": "http", "spec_version": "2.0.0"},
            ]),
        )
        # Repo empty → business branch fails → worker branch resolves.
        assert n.spec_version == "2.0.0"


class TestMixedWorkflow:
    def test_pins_each_node_independently(self):
        builtin = _node(id="b", worker_type="builtin:condition")
        worker = _node(id="w", worker_type="http")
        pinned = _node(id="p", worker_type="http", spec_version="7.0.0")
        wf = _workflow(builtin, worker, pinned)
        pin_spec_versions(
            wf,
            resolver=NodeDefinitionResolver(),
            worker_registry_client=_FakeClient([
                {"worker_type": "http", "spec_version": "2.0.0"},
            ]),
        )
        assert builtin.spec_version == "1.0.0"
        assert worker.spec_version == "2.0.0"
        assert pinned.spec_version == "7.0.0"  # author-set; preserved
