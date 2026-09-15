"""Unit tests for ``ProjectedRunState.diff`` — Phase 4.6."""

from __future__ import annotations

import pytest

from app.layer1_domain.run_projection import (
    ProjectedRunState,
)


def test_identical_states_yield_empty_diff():
    a = ProjectedRunState(
        run_id="r1", status="RUNNING",
        completed_nodes=frozenset({"n1", "n2"}),
    )
    b = ProjectedRunState(
        run_id="r1", status="RUNNING",
        completed_nodes=frozenset({"n1", "n2"}),
    )
    assert a.diff(b) == []


def test_status_divergence_detected():
    a = ProjectedRunState(run_id="r1", status="RUNNING")
    b = ProjectedRunState(run_id="r1", status="COMPLETED")
    assert a.diff(b) == ["status"]


def test_completed_nodes_divergence_detected():
    a = ProjectedRunState(run_id="r1", completed_nodes=frozenset({"n1"}))
    b = ProjectedRunState(run_id="r1", completed_nodes=frozenset({"n2"}))
    assert a.diff(b) == ["completed_nodes"]


def test_multiple_fields_diverged():
    a = ProjectedRunState(
        run_id="r1", status="RUNNING",
        completed_nodes=frozenset({"n1"}),
        variables={"k": 1},
    )
    b = ProjectedRunState(
        run_id="r1", status="COMPLETED",
        completed_nodes=frozenset({"n2"}),
        variables={"k": 2},
    )
    diverged = a.diff(b)
    assert set(diverged) == {"status", "completed_nodes", "variables"}


def test_child_runs_dict_divergence():
    a = ProjectedRunState(run_id="r1", child_runs={"n1": "child_a"})
    b = ProjectedRunState(run_id="r1", child_runs={"n1": "child_b"})
    assert a.diff(b) == ["child_runs"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
