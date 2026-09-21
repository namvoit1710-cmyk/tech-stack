"""Unit specs for OpenAIPlanner.plan (layer4 planner — the LLM seam).

Target: app/layer4_frameworks/planner/openai_planner.py

The LLM (ILLMService) and catalog (IOperationCatalog) are now injected via the
constructor (P3) — no import-time patching of a provider. Grounded:
- plan() awaits the LLM, json.loads the reply, injects user_prompt,
  and returns an ExecutionPlan domain entity.
"""
import json
import pytest

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer4_frameworks.planner.openai_planner import OpenAIPlanner


class FakeLLM:
    def __init__(self, reply=None, error=None):
        self._reply = reply
        self._error = error
        self.calls = []

    async def get_chat_completion(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if self._error:
            raise self._error
        return self._reply


class FakeCatalog:
    def list_operations(self):
        return [{"scope": "user", "description": "d", "operations": []}]


def _planner(reply=None, error=None):
    return OpenAIPlanner(llm=FakeLLM(reply=reply, error=error), catalog=FakeCatalog())


# happy path: valid JSON -> ExecutionPlan with steps + user_prompt injected
@pytest.mark.asyncio
async def test_plan_parses_llm_json_and_injects_prompt():
    reply = json.dumps(
        {"status": "ok", "execution_plan": [{"scope": "user", "odata_query": {"filter": "x eq 1"}}]}
    )
    plan = await _planner(reply=reply).plan(user_prompt="find alice")

    assert isinstance(plan, ExecutionPlan)
    assert plan.status == "ok"
    assert plan.is_supported is True
    assert plan.user_prompt == "find alice"
    assert plan.steps[0].scope == "user"
    assert plan.steps[0].odata_query.filter == "x eq 1"


# edge: a "not_supported" plan is parsed and marked unsupported
@pytest.mark.asyncio
async def test_plan_passes_through_not_supported_status():
    plan = await _planner(reply=json.dumps({"status": "not_supported"})).plan(
        user_prompt="do magic"
    )
    assert plan.status == "not_supported"
    assert plan.is_supported is False
    assert plan.user_prompt == "do magic"


# failure path: non-JSON reply -> json.loads raises
@pytest.mark.asyncio
async def test_plan_invalid_json_raises_decode_error():
    with pytest.raises(json.JSONDecodeError):
        await _planner(reply="not json at all").plan(user_prompt="x")


# boundary: empty prompt still flows and is echoed into the plan
@pytest.mark.asyncio
async def test_plan_empty_prompt_is_echoed():
    plan = await _planner(reply=json.dumps({"status": "ok"})).plan(user_prompt="")
    assert plan.user_prompt == ""


# failure path: LLM error propagates
@pytest.mark.asyncio
async def test_plan_llm_error_propagates():
    with pytest.raises(RuntimeError, match="llm timeout"):
        await _planner(error=RuntimeError("llm timeout")).plan(user_prompt="x")
