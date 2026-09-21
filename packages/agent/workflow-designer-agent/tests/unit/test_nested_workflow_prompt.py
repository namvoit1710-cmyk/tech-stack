"""SA-2132 — the designer's SYSTEM_PROMPT must carry the nested-workflow guidance.

The node PICK + config authoring is LLM-driven, so these are regression guards that the
load-bearing instructions are present (and not silently dropped in a future prompt edit),
not assertions about a live model. The deterministic behaviour lives in the classifier/gate
tests (test_failure_classifier / test_validate_repair_gate)."""
from __future__ import annotations

from app.layer4_frameworks.designer.prompts import SYSTEM_PROMPT

_P = SYSTEM_PROMPT.lower()


def test_prompt_teaches_placing_a_sub_workflow_node() -> None:
    # SA-2215: compose an existing workflow as a sub-step (not inline / not clone).
    assert "sub-workflow" in _P
    assert "reuse" in _P and "one step" in _P


def test_prompt_resolves_the_sub_workflow_id_via_find_workflows() -> None:
    # SA-2215: fill the required target workflow_id by resolving the name, never invent it.
    assert "find_workflows" in SYSTEM_PROMPT
    assert "never invent an id" in _P


def test_prompt_teaches_input_and_output_mapping() -> None:
    # SA-2216: map parent -> sub inputs and consume sub -> parent outputs.
    assert "input-mapping" in _P or "input mapping" in _P
    assert "consume its outputs" in _P
    assert "{{$input}}" in SYSTEM_PROMPT


def test_prompt_forbids_self_and_cyclic_nesting() -> None:
    # SA-2217 (design-time): don't point a sub-workflow at the current workflow; avoid cycles.
    assert "cannot invoke itself" in _P
    assert "cycle" in _P
