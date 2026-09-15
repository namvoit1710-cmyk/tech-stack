"""Unit tests for the ``ProjectedRunState`` ↔ JSON codec — Phase 4.2.

Round-trip fidelity is the only property that matters here; the codec
is a pure function feeding the snapshot store. Specific shapes covered:
defaults, frozenset fields, nested ``__file_ref`` variables, terminal
runs with error payloads.
"""

from __future__ import annotations

import json

import pytest

from app.layer1_domain.run_projection import (
    ProjectedRunState,
)


class TestRoundTrip:
    def test_default_state(self):
        state = ProjectedRunState()
        assert ProjectedRunState.from_json(state.to_json()) == state

    def test_running_state(self):
        state = ProjectedRunState(
            run_id="r1",
            workflow_id="wf1",
            status="RUNNING",
            started_at="2026-04-22T00:00:00Z",
            completed_nodes=frozenset({"n1", "n2"}),
            skipped_nodes=frozenset({"n3"}),
            child_runs={"child_node": "child_run_id"},
            variables={"n1": {"value": 42}},
        )
        assert ProjectedRunState.from_json(state.to_json()) == state

    def test_terminal_completed(self):
        state = ProjectedRunState(
            run_id="r1",
            workflow_id="wf1",
            status="COMPLETED",
            started_at="2026-04-22T00:00:00Z",
            completed_at="2026-04-22T00:05:00Z",
            output_data={"result": "ok"},
            completed_nodes=frozenset({"a", "b", "c"}),
        )
        assert ProjectedRunState.from_json(state.to_json()) == state

    def test_terminal_failed_preserves_error(self):
        state = ProjectedRunState(
            run_id="r1",
            status="FAILED",
            error="timeout on node X",
            completed_at="2026-04-22T01:00:00Z",
        )
        got = ProjectedRunState.from_json(state.to_json())
        assert got.error == "timeout on node X"
        assert got.status == "FAILED"

    def test_file_ref_variables_survive(self):
        file_ref = {
            "__file_ref": True,
            "artifact_id": "__root__",
            "file_id": "f_abc",
            "columns": ["sku", "qty"],
            "artifacts": [
                {
                    "__file_ref": True,
                    "artifact_id": "orders",
                    "file_id": "f_orders",
                    "columns": ["id", "total"],
                    "artifacts": [],
                }
            ],
        }
        state = ProjectedRunState(
            run_id="r1",
            variables={"input_node": file_ref},
        )
        got = ProjectedRunState.from_json(state.to_json())
        assert got.variables == state.variables


class TestEncodingShape:
    def test_frozensets_become_sorted_arrays(self):
        state = ProjectedRunState(
            run_id="r1",
            completed_nodes=frozenset({"z", "a", "m"}),
        )
        data = json.loads(state.to_json())
        assert data["completed_nodes"] == ["a", "m", "z"]

    def test_output_is_deterministic_for_same_state(self):
        state = ProjectedRunState(
            run_id="r1",
            completed_nodes=frozenset({"a", "b"}),
            skipped_nodes=frozenset({"c"}),
        )
        assert state.to_json() == state.to_json()


class TestForwardCompatibility:
    def test_missing_field_takes_default(self):
        partial = json.dumps({"run_id": "r1", "status": "RUNNING"})
        got = ProjectedRunState.from_json(partial)
        assert got.run_id == "r1"
        assert got.status == "RUNNING"
        assert got.completed_nodes == frozenset()
        assert got.variables == {}

    def test_empty_json_yields_default_state(self):
        got = ProjectedRunState.from_json("{}")
        assert got == ProjectedRunState()


class TestSchemaVersion:
    def test_schema_version_is_a_positive_int(self):
        assert isinstance(ProjectedRunState.SCHEMA_VERSION, int)
        assert ProjectedRunState.SCHEMA_VERSION >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
