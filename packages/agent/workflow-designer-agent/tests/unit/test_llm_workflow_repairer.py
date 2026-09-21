from __future__ import annotations

import pytest

from app.layer2_application.interfaces.workflow_repairer_interface import WorkflowRepairError
from app.layer4_frameworks.designer.llm_workflow_repairer import LlmWorkflowRepairer

DEFINITION = {"name": "wf", "description": "", "nodes": [], "edges": []}


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str, bool]] = []

    async def get_chat_completion(self, system_prompt, user_prompt, json_mode=True) -> str:
        self.calls.append((system_prompt, user_prompt, json_mode))
        return self.response


class BoomLLM:
    async def get_chat_completion(self, system_prompt, user_prompt, json_mode=True) -> str:
        raise RuntimeError("llm down")


async def test_repair_parses_plain_json_response() -> None:
    llm = FakeLLM('{"name": "fixed", "description": "", "nodes": [{"id": "a"}], "edges": []}')
    repairer = LlmWorkflowRepairer(llm)
    corrected = await repairer.repair(DEFINITION, "bad graph", {})
    assert corrected["name"] == "fixed"
    assert llm.calls[0][2] is True  # json_mode requested


async def test_repair_strips_markdown_fences() -> None:
    llm = FakeLLM('```json\n{"name": "fixed", "nodes": [], "edges": []}\n```')
    repairer = LlmWorkflowRepairer(llm)
    corrected = await repairer.repair(DEFINITION, "bad graph", {})
    assert corrected["name"] == "fixed"


async def test_repair_raises_on_non_json_response() -> None:
    llm = FakeLLM("not json at all")
    repairer = LlmWorkflowRepairer(llm)
    with pytest.raises(WorkflowRepairError):
        await repairer.repair(DEFINITION, "bad graph", {})


async def test_repair_raises_when_required_keys_missing() -> None:
    llm = FakeLLM('{"name": "fixed"}')
    repairer = LlmWorkflowRepairer(llm)
    with pytest.raises(WorkflowRepairError):
        await repairer.repair(DEFINITION, "bad graph", {})


async def test_repair_raises_when_llm_call_fails() -> None:
    repairer = LlmWorkflowRepairer(BoomLLM())
    with pytest.raises(WorkflowRepairError):
        await repairer.repair(DEFINITION, "bad graph", {})
