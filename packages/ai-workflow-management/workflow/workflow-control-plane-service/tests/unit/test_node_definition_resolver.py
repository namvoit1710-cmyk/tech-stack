"""Tests for NodeDefinitionResolver — inheritance chain resolution."""

import pytest
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.definition.node_definition_resolver import (
    NodeDefinitionResolver,
    ResolvedNodeDefinition,
    MAX_INHERITANCE_DEPTH,
)
from app.layer1_domain.exceptions import (
    CircularInheritanceError,
    InheritanceDepthExceededError,
)


@pytest.fixture
def resolver():
    return NodeDefinitionResolver()


class TestResolveNoParent:
    """A definition with no parent should resolve identically to current behavior."""

    def test_single_definition_resolves(self, resolver, node_definition_repo):
        nd = NodeDefinition(
            id="nd-1", tenant_id="t1", name="Root",
            base_worker_id="w1",
            locked_defaults={"key": "val"},
            input_schema=[{"key": "f1", "type": "string"}],
            output_schema=[{"key": "o1", "type": "number"}],
            input_mapping={"worker_field": "{{$custom.f1}}"},
            output_mapping={"out": "{{$_output.o1}}"},
            icon="Star", color="#FF0000",
            tags=["a", "b"],
            data_mode="csv_single",
            edit_mode="before_dispatch",
        )
        node_definition_repo.save(nd)

        result = resolver.resolve("nd-1", node_definition_repo)

        assert result.base_worker_id == "w1"
        assert result.locked_defaults == {"key": "val"}
        assert result.input_schema == [{"key": "f1", "type": "string"}]
        assert result.output_schema == [{"key": "o1", "type": "number"}]
        assert result.input_mapping == {"worker_field": "{{$custom.f1}}"}
        assert result.output_mapping == {"out": "{{$_output.o1}}"}
        assert result.icon == "Star"
        assert result.color == "#FF0000"
        assert result.tags == ["a", "b"]
        assert result.data_mode == "csv_single"
        assert result.edit_mode == "before_dispatch"

    def test_missing_definition_returns_empty(self, resolver, node_definition_repo):
        result = resolver.resolve("nonexistent", node_definition_repo)
        assert result == ResolvedNodeDefinition()


class TestResolveTwoLevelChain:
    """Parent + child merge."""

    def test_child_overrides_parent(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="parent", tenant_id="t1", name="Parent",
            base_worker_id="w1",
            locked_defaults={"gateway": "stripe", "currency": "USD"},
            input_schema=[{"key": "amount", "type": "number"}],
            output_schema=[{"key": "txn_id", "type": "string"}],
            input_mapping={"charge_amount": "{{$custom.amount}}"},
            output_mapping={"txn_id": "{{$_output.transaction_id}}"},
            icon="CreditCard", color="#10B981",
            tags=["payment", "finance"],
            data_mode="json",
            edit_mode="none",
        )
        child = NodeDefinition(
            id="child", tenant_id="t1", name="Child",
            parent_definition_id="parent",
            locked_defaults={"payment_tier": "premium"},
            input_schema=[{"key": "amount", "type": "number"}, {"key": "priority", "type": "string"}],
            input_mapping={"metadata": {"priority": "{{$custom.priority}}"}},
            output_mapping={},
            icon="Crown", color="#F59E0B",
            tags=["payment", "premium"],
            edit_mode="before_dispatch",
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("child", node_definition_repo)

        # locked_defaults: shallow merge, child adds payment_tier
        assert result.locked_defaults == {
            "gateway": "stripe", "currency": "USD", "payment_tier": "premium",
        }
        # input_schema: child replaces (non-empty)
        assert len(result.input_schema) == 2
        assert result.input_schema[0]["key"] == "amount"
        assert result.input_schema[1]["key"] == "priority"
        # output_schema: child has empty, inherits parent's
        assert result.output_schema == [{"key": "txn_id", "type": "string"}]
        # input_mapping: shallow merge
        assert result.input_mapping == {
            "charge_amount": "{{$custom.amount}}",
            "metadata": {"priority": "{{$custom.priority}}"},
        }
        # output_mapping: child has empty dict, parent's values still present
        assert result.output_mapping == {"txn_id": "{{$_output.transaction_id}}"}
        # base_worker_id: inherited from parent (child has None)
        assert result.base_worker_id == "w1"
        # icon/color: child wins
        assert result.icon == "Crown"
        assert result.color == "#F59E0B"
        # tags: union
        assert set(result.tags) == {"payment", "finance", "premium"}
        # data_mode: child is "json" (default), keeps parent's "json"
        assert result.data_mode == "json"
        # edit_mode: child is "before_dispatch" (not default "none")
        assert result.edit_mode == "before_dispatch"


class TestResolveThreeLevelChain:
    """Root → middle → leaf chain."""

    def test_three_level_merge(self, resolver, node_definition_repo):
        root = NodeDefinition(
            id="root", tenant_id="t1", name="Root",
            base_worker_id="w1",
            locked_defaults={"a": 1},
            tags=["root"],
        )
        middle = NodeDefinition(
            id="middle", tenant_id="t1", name="Middle",
            parent_definition_id="root",
            locked_defaults={"b": 2},
            tags=["middle"],
        )
        leaf = NodeDefinition(
            id="leaf", tenant_id="t1", name="Leaf",
            parent_definition_id="middle",
            locked_defaults={"a": 99, "c": 3},
            tags=["leaf"],
        )
        for nd in [root, middle, leaf]:
            node_definition_repo.save(nd)

        result = resolver.resolve("leaf", node_definition_repo)

        assert result.locked_defaults == {"a": 99, "b": 2, "c": 3}
        assert result.base_worker_id == "w1"
        assert result.tags == ["root", "middle", "leaf"]


class TestCycleDetection:

    def test_direct_cycle(self, resolver, node_definition_repo):
        a = NodeDefinition(id="a", tenant_id="t1", name="A", parent_definition_id="b", base_worker_id="w")
        b = NodeDefinition(id="b", tenant_id="t1", name="B", parent_definition_id="a", base_worker_id="w")
        node_definition_repo.save(a)
        node_definition_repo.save(b)

        with pytest.raises(CircularInheritanceError):
            resolver.resolve("a", node_definition_repo)

    def test_self_reference_in_chain(self, resolver, node_definition_repo):
        a = NodeDefinition(id="a", tenant_id="t1", name="A", parent_definition_id="a", base_worker_id="w")
        node_definition_repo.save(a)

        with pytest.raises(CircularInheritanceError):
            resolver.resolve("a", node_definition_repo)

    def test_validate_parent_chain_cycle(self, resolver, node_definition_repo):
        a = NodeDefinition(id="a", tenant_id="t1", name="A", base_worker_id="w")
        b = NodeDefinition(id="b", tenant_id="t1", name="B", parent_definition_id="a", base_worker_id="w")
        node_definition_repo.save(a)
        node_definition_repo.save(b)

        # Try to make A point to B — would create cycle A→B→A
        with pytest.raises(CircularInheritanceError):
            resolver.validate_parent_chain("a", "b", node_definition_repo)

    def test_validate_parent_chain_self_reference(self, resolver, node_definition_repo):
        with pytest.raises(CircularInheritanceError):
            resolver.validate_parent_chain("x", "x", node_definition_repo)


class TestMaxDepth:

    def test_exceeds_max_depth(self, resolver, node_definition_repo):
        # Create chain of MAX_INHERITANCE_DEPTH + 2 nodes
        prev_id = None
        for i in range(MAX_INHERITANCE_DEPTH + 2):
            nd = NodeDefinition(
                id=f"nd-{i}", tenant_id="t1", name=f"ND-{i}",
                parent_definition_id=prev_id,
                base_worker_id="w1" if i == 0 else None,
            )
            node_definition_repo.save(nd)
            prev_id = nd.id

        with pytest.raises(InheritanceDepthExceededError):
            resolver.resolve(prev_id, node_definition_repo)

    def test_exactly_at_max_depth_succeeds(self, resolver, node_definition_repo):
        # Chain of exactly MAX_INHERITANCE_DEPTH + 1 nodes (depth = MAX_INHERITANCE_DEPTH)
        prev_id = None
        for i in range(MAX_INHERITANCE_DEPTH + 1):
            nd = NodeDefinition(
                id=f"nd-{i}", tenant_id="t1", name=f"ND-{i}",
                parent_definition_id=prev_id,
                base_worker_id="w1" if i == 0 else None,
            )
            node_definition_repo.save(nd)
            prev_id = nd.id

        result = resolver.resolve(prev_id, node_definition_repo)
        assert result.base_worker_id == "w1"


class TestMergeStrategies:

    def test_child_base_worker_id_overrides_parent(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="p", tenant_id="t1", name="P", base_worker_id="w1",
        )
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="p", base_worker_id="w2",
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.base_worker_id == "w2"

    def test_child_data_mode_default_inherits_parent(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="p", tenant_id="t1", name="P", base_worker_id="w1",
            data_mode="csv_single",
        )
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="p",
            data_mode="json",  # default — should not override
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.data_mode == "csv_single"

    def test_child_edit_mode_default_inherits_parent(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="p", tenant_id="t1", name="P", base_worker_id="w1",
            edit_mode="before_dispatch",
        )
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="p",
            edit_mode="none",  # default — should not override
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.edit_mode == "before_dispatch"

    def test_tags_are_deduplicated_union(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="p", tenant_id="t1", name="P", base_worker_id="w1",
            tags=["a", "b", "c"],
        )
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="p",
            tags=["b", "c", "d"],  # b, c overlap
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.tags == ["a", "b", "c", "d"]

    def test_empty_child_inherits_parent_icon_color(self, resolver, node_definition_repo):
        parent = NodeDefinition(
            id="p", tenant_id="t1", name="P", base_worker_id="w1",
            icon="Star", color="#FF0000",
        )
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="p",
            icon="", color="",
        )
        node_definition_repo.save(parent)
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.icon == "Star"
        assert result.color == "#FF0000"

    def test_broken_parent_link_resolves_partial(self, resolver, node_definition_repo):
        """If parent doesn't exist, resolve with just the leaf."""
        child = NodeDefinition(
            id="c", tenant_id="t1", name="C",
            parent_definition_id="nonexistent",
            base_worker_id="w1",
            locked_defaults={"key": "val"},
        )
        node_definition_repo.save(child)

        result = resolver.resolve("c", node_definition_repo)
        assert result.base_worker_id == "w1"
        assert result.locked_defaults == {"key": "val"}


class TestSpecVersionResolution:
    """Phase 0 formalization of the unversioned-reference resolution rule."""

    def test_resolve_returns_leaf_spec_version_when_none_pinned(
        self, resolver, node_definition_repo,
    ):
        nd = NodeDefinition(
            id="nd-a", tenant_id="t1", name="A",
            base_worker_id="w1", spec_version="2.4.1",
        )
        node_definition_repo.save(nd)
        result = resolver.resolve("nd-a", node_definition_repo)
        assert result.resolved_spec_version == "2.4.1"

    def test_resolve_defaults_when_leaf_missing_spec_version(
        self, resolver, node_definition_repo,
    ):
        """Missing leaf returns empty ResolvedNodeDefinition with default."""
        result = resolver.resolve("missing", node_definition_repo)
        assert result.resolved_spec_version == "1.0.0"

    def test_resolve_accepts_pinned_spec_version_matching_leaf(
        self, resolver, node_definition_repo,
    ):
        nd = NodeDefinition(
            id="nd-a", tenant_id="t1", name="A",
            base_worker_id="w1", spec_version="3.0.0",
        )
        node_definition_repo.save(nd)
        result = resolver.resolve("nd-a", node_definition_repo, spec_version="3.0.0")
        assert result.resolved_spec_version == "3.0.0"

    def test_resolve_rejects_pinned_spec_version_mismatch(
        self, resolver, node_definition_repo,
    ):
        nd = NodeDefinition(
            id="nd-a", tenant_id="t1", name="A",
            base_worker_id="w1", spec_version="2.0.0",
        )
        node_definition_repo.save(nd)
        with pytest.raises(ValueError, match="spec_version mismatch"):
            resolver.resolve("nd-a", node_definition_repo, spec_version="1.0.0")

    def test_resolve_worker_spec_version_returns_pinned(self, resolver):
        workers = [
            {"worker_type": "http", "spec_version": "1.0.0"},
            {"worker_type": "http", "spec_version": "2.5.0"},
        ]
        assert resolver.resolve_worker_spec_version("http", "1.0.0", workers) == "1.0.0"

    def test_resolve_worker_spec_version_picks_max_when_unpinned(self, resolver):
        workers = [
            {"worker_type": "http", "spec_version": "1.0.0"},
            {"worker_type": "http", "spec_version": "2.10.0"},
            {"worker_type": "http", "spec_version": "2.9.5"},
            {"worker_type": "other", "spec_version": "9.9.9"},
        ]
        # 2.10.0 > 2.9.5 numerically; string-max would give "2.9.5".
        assert resolver.resolve_worker_spec_version("http", None, workers) == "2.10.0"

    def test_resolve_worker_spec_version_default_when_no_match(self, resolver):
        workers = [{"worker_type": "other", "spec_version": "5.0.0"}]
        assert resolver.resolve_worker_spec_version("http", None, workers) == "1.0.0"

    def test_resolve_worker_spec_version_missing_field_defaults(self, resolver):
        workers = [{"worker_type": "http"}]  # no spec_version
        assert resolver.resolve_worker_spec_version("http", None, workers) == "1.0.0"
