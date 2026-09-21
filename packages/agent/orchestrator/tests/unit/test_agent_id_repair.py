"""Regression tests for orchestrator agent-id repair.

The task-breakdown LLM must copy a 36-char agent UUID into `execute_by` and often
mis-transcribes or invents it. `_resolve_planned_agent_ids` snaps each picked id back to a
real registry id before dispatch (exact → closest → leave-for-clean-fail).
"""
from dataclasses import dataclass

from app.layer1_domain.entities.planner.task_breakdown_response import (
    ParsedSharedStateTaskModel,
    TaskBreakdownResponse,
)
from tests.unit.test_agent_memory import _build_use_case


@dataclass
class _Agent:
    id: str
    name: str = ""
    is_published: bool = True
    is_alive: bool = True


REAL = [
    _Agent("019ef88e-0a41-7976-a2e4-979e7d0a0daa"),
    _Agent("019edeb1-b385-79ea-aad6-392b4aaa343d"),
    _Agent("019ef3bd-f6de-7adb-8003-e47fbf9ab416"),
]


def _plan(execute_by):
    return TaskBreakdownResponse(tasks=[ParsedSharedStateTaskModel(id="t1", goal="g", execute_by=execute_by)])


def test_valid_id_is_kept_and_backfills_agent_id():
    uc = _build_use_case()
    plan = _plan("019ef88e-0a41-7976-a2e4-979e7d0a0daa")
    uc._resolve_planned_agent_ids(plan, REAL)
    assert plan.tasks[0].execute_by == "019ef88e-0a41-7976-a2e4-979e7d0a0daa"
    assert plan.tasks[0].agent_id == "019ef88e-0a41-7976-a2e4-979e7d0a0daa"


def test_agent_prefix_is_stripped():
    uc = _build_use_case()
    plan = _plan("agent.019ef88e-0a41-7976-a2e4-979e7d0a0daa")
    uc._resolve_planned_agent_ids(plan, REAL)
    assert plan.tasks[0].execute_by == "019ef88e-0a41-7976-a2e4-979e7d0a0daa"


def test_single_char_corruption_is_repaired():
    uc = _build_use_case()
    # extra 'e' — exactly the live failure (019eef88e… vs real 019ef88e…)
    plan = _plan("019eef88e-0a41-7976-a2e4-979e7d0a0daa")
    uc._resolve_planned_agent_ids(plan, REAL)
    assert plan.tasks[0].execute_by == "019ef88e-0a41-7976-a2e4-979e7d0a0daa"


def test_fully_hallucinated_id_is_left_as_is():
    # A fully invalid id (not a near-miss) is left unchanged; the downstream None-guard
    # fails cleanly rather than snapping to an unrelated agent (matches the solace
    # LLM-ambiguity design — no token-scoring fallback).
    uc = _build_use_case()
    plan = _plan("ffffffff-ffff-ffff-ffff-ffffffffffff")
    uc._resolve_planned_agent_ids(plan, REAL)
    assert plan.tasks[0].execute_by == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_no_agents_is_a_noop():
    uc = _build_use_case()
    plan = _plan("019eef88e-0a41-7976-a2e4-979e7d0a0daa")
    uc._resolve_planned_agent_ids(plan, [])
    assert plan.tasks[0].execute_by == "019eef88e-0a41-7976-a2e4-979e7d0a0daa"
