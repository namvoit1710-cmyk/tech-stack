from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer1_domain.entities.planner.task_breakdown_response import (
    StrategicWorkflowPlan,
    TaskBreakdownResponse,
)
from app.layer4_frameworks.ai.openai_llm_provider import OpenAILLMProvider


class TestOpenAILLMProvider:
    def test_complete_with_format_uses_openai_parse_for_pydantic_models(self):
        class ParsedResponse(BaseModel):
            answer: str

        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        expected = ParsedResponse(answer="done")
        provider._client.chat.completions.parse.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=expected))]
        )

        result = provider.complete_with_format(
            messages=[{"role": "user", "content": "respond"}],
            response_model=ParsedResponse,
        )

        assert result == expected
        provider._client.chat.completions.create.assert_not_called()
        provider._client.chat.completions.parse.assert_called_once()

    def test_complete_with_format_materializes_dataclass_response(self):
        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"context_summary": "Summarize", "logical_steps": ["A", "B"], "dependency_rules": "B after A"}'
                    )
                )
            ]
        )

        result = provider.complete_with_format(
            messages=[{"role": "user", "content": "plan"}],
            response_model=StrategicWorkflowPlan,
        )

        assert result == StrategicWorkflowPlan(
            context_summary="Summarize",
            logical_steps=["A", "B"],
            dependency_rules="B after A",
        )
        provider._client.chat.completions.parse.assert_not_called()
        provider._client.chat.completions.create.assert_called_once()

    def test_complete_with_format_materializes_nested_dataclasses(self):
        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"reasoning": "Sequential", "tasks": ['
                            '{"id": "task-1", "goal": "Validate file", "execute_by": "agent-a", '
                            '"input_schema": ["file"], "output_schema": ["validated"], '
                            '"status": "PENDING", "input_data": {}, "output_data": {}, '
                            '"depend_on": [], "workflow_id": "wf-1", "agent_id": "agent-a"}'
                            ']}'
                        )
                    )
                )
            ]
        )

        result = provider.complete_with_format(
            messages=[{"role": "user", "content": "break this down"}],
            response_model=TaskBreakdownResponse,
        )

        assert isinstance(result, TaskBreakdownResponse)
        assert result.reasoning == "Sequential"
        assert len(result.tasks) == 1
        assert result.tasks[0].goal == "Validate file"
        assert result.tasks[0].status is TaskStatus.PENDING

    def test_complete_with_format_wraps_single_object_for_list_field(self):
        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"reasoning": "Sequential", "tasks": '
                            '{"id": "task-1", "goal": "Validate file", "execute_by": "agent-a", '
                            '"input_schema": ["file"], "output_schema": ["validated"], '
                            '"status": "PENDING", "input_data": {}, "output_data": {}, '
                            '"depend_on": [], "workflow_id": "wf-1", "agent_id": "agent-a"}'
                            '}'
                        )
                    )
                )
            ]
        )

        result = provider.complete_with_format(
            messages=[{"role": "user", "content": "break this down"}],
            response_model=TaskBreakdownResponse,
        )

        assert isinstance(result, TaskBreakdownResponse)
        assert len(result.tasks) == 1
        assert result.tasks[0].goal == "Validate file"

    def test_complete_with_format_raises_for_invalid_dataclass_json(self):
        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]
        )

        with pytest.raises(ValueError, match="invalid JSON"):
            provider.complete_with_format(
                messages=[{"role": "user", "content": "plan"}],
                response_model=StrategicWorkflowPlan,
            )

    def test_complete_with_format_surfaces_usage_to_observer_for_parse_path(self):
        class ParsedResponse(BaseModel):
            answer: str

        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        usage = SimpleNamespace(total_tokens=42)
        provider._client.chat.completions.parse.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=ParsedResponse(answer="done")))],
            usage=usage,
        )
        observed = []
        provider.usage_observer = observed.append

        provider.complete_with_format(
            messages=[{"role": "user", "content": "respond"}],
            response_model=ParsedResponse,
        )

        assert observed == [usage]

    def test_complete_with_format_surfaces_usage_to_observer_for_json_path(self):
        provider = OpenAILLMProvider(api_key="test-key")
        provider._client = MagicMock()
        usage = SimpleNamespace(total_tokens=17)
        provider._client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"context_summary": "Summarize", "logical_steps": ["A"], "dependency_rules": "none"}'
                    )
                )
            ],
            usage=usage,
        )
        observed = []
        provider.usage_observer = observed.append

        provider.complete_with_format(
            messages=[{"role": "user", "content": "plan"}],
            response_model=StrategicWorkflowPlan,
        )

        assert observed == [usage]