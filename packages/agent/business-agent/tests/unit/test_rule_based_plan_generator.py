"""Unit specs for RuleBasedPlanGenerator (layer4 planner) — the Software-5.0
"recipe" alternative to the LLM planners (agent-subsystem canon invariant 10).

WHITE-BOX FINDINGS (grounded in source, left visible per the unit-testing
discipline — never edited/hidden):

  FINDING A1-1 (real bug, import-breaking): the module
    app/layer4_frameworks/planners/rule_based_plan_generator.py line 3 does
        from app.layer1_domain.entities.execution_plan import ExecutionPlan
    but app/layer1_domain/entities/execution_plan.py DOES NOT EXIST. Importing
    the module raises ModuleNotFoundError, so RuleBasedPlanGenerator cannot be
    constructed at all. It is also never wired into PlannerResolver (the RULE
    branch raises "No rule planner provided"), so this code is currently dead.

  FINDING A1-2 (real bug, contract violation): even ignoring the import,
    RuleBasedPlanGenerator.generate builds a `steps` list then unconditionally
    `return None` (source lines 16-37). Its declared return type is
    PlannerResponse; it returns None and discards the steps it built. Any caller
    doing `plan.type` on the result would hit AttributeError on None.

These are captured as executable evidence. The first test asserts the CURRENT
broken import (documents the bug without pretending it works). The second test
encodes the INTENDED contract and is xfail — it will start passing (xpass) the
moment both bugs are fixed, flagging the fix for review.
"""

import importlib

import pytest


_MODULE = "app.layer4_frameworks.planners.rule_based_plan_generator"


# --- FINDING A1-1: the module cannot be imported (missing execution_plan) ---
def test_module_import_fails_missing_execution_plan_entity():
    with pytest.raises(ModuleNotFoundError) as exc:
        importlib.import_module(_MODULE)

    assert "execution_plan" in str(exc.value)


# --- FINDING A1-2 (intended contract, xfail while bugs A1-1/A1-2 stand) ---
# When the missing entity is added AND generate() returns a real PlannerResponse
# built from `steps`, this spec should pass and xpass will surface the fix.
@pytest.mark.xfail(
    reason="A1-1 missing execution_plan module + A1-2 generate() returns None",
    raises=(ModuleNotFoundError, AssertionError, AttributeError),
    strict=False,
)
@pytest.mark.asyncio
async def test_generate_maps_one_step_per_tool_intended_contract():
    from types import SimpleNamespace

    rbpg = importlib.import_module(_MODULE)
    generator = rbpg.RuleBasedPlanGenerator()

    orches_plan = SimpleNamespace(execute_by="agent.agent-1")
    tools = [{"name": "tool-a"}, {"name": "tool-b"}]

    result = await generator.generate(
        orches_plan=orches_plan,
        business_intent="do the thing",
        tools=tools,
    )

    # intended: a PlannerResponse (not None) carrying one step per tool
    assert result is not None
    assert len(result.steps) == 2


# --- FINDING A1-2 (fallback branch, xfail while bugs stand) ---
@pytest.mark.xfail(
    reason="A1-1 missing execution_plan module + A1-2 generate() returns None",
    raises=(ModuleNotFoundError, AssertionError, AttributeError),
    strict=False,
)
@pytest.mark.asyncio
async def test_generate_falls_back_to_noop_when_no_tools_intended_contract():
    from types import SimpleNamespace

    rbpg = importlib.import_module(_MODULE)
    generator = rbpg.RuleBasedPlanGenerator()

    result = await generator.generate(
        orches_plan=SimpleNamespace(execute_by="agent.agent-1"),
        business_intent="do the thing",
        tools=[],
    )

    assert result is not None
    assert len(result.steps) == 1
