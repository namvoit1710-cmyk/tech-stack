"""Phase M.3.1 — broker orchestration queue serialization round-trip.

Every orchestration action type must survive ``asdict -> JSON -> dict ->
_deserialize``. Pre-M.3 the Kafka path silently dropped
``EditNodeDataAction`` because it was missing from ``_ACTION_CLASSES``;
this file pins that and guards against future omissions.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ContinueRunAction,
    EditNodeDataAction,
    ExecuteNodeAction,
    FailTaskAction,
    LoopIterationAction,
    ResumeRunAction,
    ResumeTaskAction,
    StartRunAction,
)
from app.layer4_frameworks.orchestration_queue.kafka_queue import (
    _ACTION_CLASSES,
    _deserialize,
)


class TestActionRegistry:
    def test_every_action_type_is_registered(self):
        # Each dataclass above has a unique ``action_type`` default; every
        # one must appear in ``_ACTION_CLASSES`` so the broker consumer
        # can re-materialise it.
        all_actions = [
            StartRunAction(run_id="r"),
            ContinueRunAction(run_id="r"),
            FailTaskAction(run_id="r"),
            ResumeTaskAction(run_id="r"),
            ExecuteNodeAction(run_id="r"),
            LoopIterationAction(run_id="r"),
            ResumeRunAction(run_id="r"),
            EditNodeDataAction(run_id="r"),
        ]
        for action in all_actions:
            assert action.action_type in _ACTION_CLASSES, (
                f"{type(action).__name__} ({action.action_type}) missing "
                f"from _ACTION_CLASSES"
            )


class TestRoundTrip:
    def _roundtrip(self, action):
        payload = json.loads(json.dumps(asdict(action)))
        return _deserialize(payload)

    def test_start_run_action(self):
        out = self._roundtrip(StartRunAction(run_id="r-1"))
        assert isinstance(out, StartRunAction)
        assert out.run_id == "r-1"

    def test_continue_run_action_preserves_fields(self):
        out = self._roundtrip(
            ContinueRunAction(run_id="r-1", task_id="t-1", test_mode="unit")
        )
        assert isinstance(out, ContinueRunAction)
        assert out.task_id == "t-1"
        assert out.test_mode == "unit"

    def test_loop_iteration_action_preserves_fields(self):
        out = self._roundtrip(
            LoopIterationAction(
                run_id="r-1", task_id="t-1",
                loop_node_id="loop-a", iteration_index=3,
            )
        )
        assert isinstance(out, LoopIterationAction)
        assert out.loop_node_id == "loop-a"
        assert out.iteration_index == 3

    def test_edit_node_data_action_rehydrates_command_dataclass(self):
        # The on-the-wire shape is ``command: dict``; after _deserialize
        # the worker must see ``command: EditNodeDataCommand``.
        cmd = EditNodeDataCommand(
            run_id="r-1", node_id="node-a", task_id="t-1",
            if_match="W/\"abc\"",
            artifacts=[{
                "artifact_id": "__root__",
                "operations": [{
                    "operation": "insert", "row_id": None,
                    "data": {"foo": "bar"},
                }],
            }],
        )
        action_in = EditNodeDataAction(
            run_id="r-1", correlation_id="corr-1", command=cmd,
        )
        out = self._roundtrip(action_in)
        assert isinstance(out, EditNodeDataAction)
        assert out.correlation_id == "corr-1"
        assert isinstance(out.command, EditNodeDataCommand)
        assert out.command.node_id == "node-a"
        assert out.command.artifacts == [{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"foo": "bar"},
            }],
        }]

    def test_unknown_action_type_returns_none(self):
        assert _deserialize({"action_type": "nope", "run_id": "r"}) is None

    def test_malformed_edit_node_data_command_returns_none(self):
        # ``command`` missing entirely still produces a valid action
        # (command=None default); an invalid dict shape falls through the
        # ``TypeError`` branch and returns None.
        bad = {
            "action_type": "edit_node_data",
            "run_id": "r-1",
            "correlation_id": "c",
            "command": {"nonexistent_field": 1},
        }
        assert _deserialize(bad) is None
