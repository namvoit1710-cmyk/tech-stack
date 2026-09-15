"""Unit tests for ``GraphTopology.downstream_to_skip`` / ``downstream_to_reset``."""

from __future__ import annotations

from app.layer1_domain.entities.workflow_run import GraphTopology


class TestCollectDownstreamNodesToSkip:
    def test_single_node_no_successors(self):
        topology = GraphTopology(
            adjacency={"A": []},
            predecessors={"A": []},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["A"],
        )
        result = topology.downstream_to_skip("A")
        assert result == ["A"]

    def test_linear_chain(self):
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["C"], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["B"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["C"],
        )
        result = topology.downstream_to_skip("A")
        assert result == ["A", "B", "C"]

    def test_merge_point_all_preds_skipped(self):
        """When both predecessors of a merge node are skipped, the merge is skipped too."""
        topology = GraphTopology(
            adjacency={"A": ["M"], "B": ["M"], "M": []},
            predecessors={"A": [], "B": [], "M": ["A", "B"]},
            edge_map={},
            entry_nodes=["A", "B"],
            leaf_nodes=["M"],
        )
        # Skip starting from A, with B already skipped
        result = topology.downstream_to_skip("A", already_skipped={"B"})
        assert "A" in result
        assert "M" in result

    def test_merge_point_not_all_preds_skipped(self):
        """When only one predecessor of a merge node is skipped, the merge is NOT skipped."""
        topology = GraphTopology(
            adjacency={"A": ["M"], "B": ["M"], "M": []},
            predecessors={"A": [], "B": [], "M": ["A", "B"]},
            edge_map={},
            entry_nodes=["A", "B"],
            leaf_nodes=["M"],
        )
        # Skip starting from A, but B is NOT skipped
        result = topology.downstream_to_skip("A")
        assert "A" in result
        assert "M" not in result

    def test_already_skipped_helps_satisfy_predecessors(self):
        """Pre-existing skipped nodes contribute to the predecessor check."""
        topology = GraphTopology(
            adjacency={"X": ["C"], "Y": ["C"], "C": ["D"], "D": []},
            predecessors={"X": [], "Y": [], "C": ["X", "Y"], "D": ["C"]},
            edge_map={},
            entry_nodes=["X", "Y"],
            leaf_nodes=["D"],
        )
        result = topology.downstream_to_skip("X", already_skipped={"Y"})
        assert result == ["X", "C", "D"]

    def test_diamond_shape(self):
        """Diamond: A -> B, A -> C, B -> D, C -> D. Starting from A skips everything."""
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["D"],
        )
        result = topology.downstream_to_skip("A")
        assert set(result) == {"A", "B", "C", "D"}


class TestCollectDownstreamNodesToReset:
    """Cascade-rerun downstream traversal.

    Differs from skip: resets every reachable descendant, NOT gated on
    all-predecessors-skipped. Once an upstream output changes, every
    descendant is stale.
    """

    def test_single_node_no_successors(self):
        topology = GraphTopology(
            adjacency={"A": []},
            predecessors={"A": []},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["A"],
        )
        assert topology.downstream_to_reset("A") == ["A"]

    def test_linear_chain_includes_every_descendant(self):
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["C"], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["B"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["C"],
        )
        assert topology.downstream_to_reset("A") == ["A", "B", "C"]

    def test_branching_visits_all_branches(self):
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": ["D"], "C": ["E"], "D": [], "E": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"], "D": ["B"], "E": ["C"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["D", "E"],
        )
        result = topology.downstream_to_reset("A")
        assert set(result) == {"A", "B", "C", "D", "E"}
        # Start node is first; remaining order follows BFS.
        assert result[0] == "A"

    def test_merge_point_reset_even_without_all_preds_stale(self):
        """Key divergence from *_to_skip: D is reset even though only B is
        traversed (C is unaffected) — because B's output feeding D is enough
        to make D stale."""
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["D"], "C": ["D"], "D": []},
            predecessors={"A": [], "B": ["A"], "C": [], "D": ["B", "C"]},
            edge_map={},
            entry_nodes=["A", "C"],
            leaf_nodes=["D"],
        )
        result = topology.downstream_to_reset("A")
        assert set(result) == {"A", "B", "D"}
        assert "C" not in result

    def test_include_start_false_excludes_start_node(self):
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["C"], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["B"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["C"],
        )
        result = topology.downstream_to_reset("A", include_start=False)
        assert result == ["B", "C"]

    def test_start_node_not_in_adjacency_returns_just_itself(self):
        topology = GraphTopology(
            adjacency={},  # empty
            predecessors={},
            edge_map={},
            entry_nodes=[],
            leaf_nodes=[],
        )
        assert topology.downstream_to_reset("X") == ["X"]

    def test_diamond_dag_each_node_visited_once(self):
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]},
            edge_map={},
            entry_nodes=["A"],
            leaf_nodes=["D"],
        )
        result = topology.downstream_to_reset("A")
        # No duplicates even though D is reached twice.
        assert len(result) == len(set(result))
        assert set(result) == {"A", "B", "C", "D"}
