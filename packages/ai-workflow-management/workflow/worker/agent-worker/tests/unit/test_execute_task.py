import json
from unittest.mock import MagicMock

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer1_domain.value_objects.ai_provider import AiProvider
from app.layer1_domain.value_objects.ai_role import AiRole
from app.layer1_domain.value_objects.response_format import ResponseFormat
from app.layer1_domain.entities.ai_completion_response import AiCompletionResponse
from app.layer1_domain.entities.ai_usage import AiUsage
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    AiProviderFactory,
    AGENT_INPUT_SCHEMA,
    AGENT_OUTPUT_SCHEMA,
)


def _mock_completion_response(
    content: str = "Hello!",
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
) -> AiCompletionResponse:
    return AiCompletionResponse(
        content=content,
        model=model,
        usage=AiUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        finish_reason=finish_reason,
    )


def _mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.chat_completion.return_value = _mock_completion_response()
    return provider


def _mock_factory(provider: MagicMock) -> AiProviderFactory:
    factory = MagicMock(spec=AiProviderFactory)
    factory.create.return_value = provider
    return factory


class TestBasicCompletion:
    def test_simple_completion(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t1",
            action="agent",
            inputs={
                "provider": AiProvider.OPENAI.value,
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Say hello",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_content"] == "Hello!"
        assert result.outputs["model_used"] == "gpt-4o"
        assert result.outputs["finish_reason"] == "stop"
        assert result.outputs["prompt_tokens"] == 10
        assert result.outputs["completion_tokens"] == 5
        assert result.outputs["total_tokens"] == 15
        assert result.outputs["provider_used"] == "openai"
        assert result.outputs["elapsed_ms"] > 0
        factory.create.assert_called_once_with("openai", "sk-test")


class TestSystemPrompt:
    def test_system_prompt_included(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t3",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "system_prompt": "You are a pirate.",
                "user_prompt": "Greet me.",
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        assert len(call_args.messages) == 2
        assert call_args.messages[0].role == AiRole.SYSTEM
        assert call_args.messages[0].content == "You are a pirate."
        assert call_args.messages[1].role == AiRole.USER

    def test_no_system_prompt(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t4",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        assert len(call_args.messages) == 1
        assert call_args.messages[0].role == AiRole.USER


class TestPromptVariables:
    def test_variable_substitution(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t5",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Translate {{text}} to {{language}}",
                "prompt_variables": [
                    {"variable_name": "text", "variable_value": "Hello"},
                    {"variable_name": "language", "variable_value": "French"},
                ],
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        user_msg = call_args.messages[-1]
        assert user_msg.content == "Translate Hello to French"

    def test_no_variables(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t6",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello world",
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        assert call_args.messages[-1].content == "Hello world"


class TestConversationHistory:
    def test_history_ordering(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t7",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "system_prompt": "Be helpful.",
                "conversation_history": [
                    {"role": "user", "content": "What is 2+2?"},
                    {"role": "assistant", "content": "4"},
                ],
                "user_prompt": "And 3+3?",
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        msgs = call_args.messages
        assert len(msgs) == 4
        assert msgs[0].role == AiRole.SYSTEM
        assert msgs[1].role == AiRole.USER
        assert msgs[1].content == "What is 2+2?"
        assert msgs[2].role == AiRole.ASSISTANT
        assert msgs[2].content == "4"
        assert msgs[3].role == AiRole.USER
        assert msgs[3].content == "And 3+3?"


class TestModelParameters:
    def test_custom_parameters(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t8",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
                "temperature": 0.2,
                "max_tokens": 2048,
                "top_p": 0.9,
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        assert call_args.temperature == 0.2
        assert call_args.max_tokens == 2048
        assert call_args.top_p == 0.9

    def test_default_parameters(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t9",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        call_args = provider.chat_completion.call_args[0][0]
        assert call_args.temperature == 0.7
        assert call_args.max_tokens == 1024
        assert call_args.top_p == 1.0


class TestResponseFormat:
    def test_json_response_format(self, logger, monitor):
        provider = _mock_provider()
        provider.chat_completion.return_value = _mock_completion_response(
            content='{"key": "value"}'
        )
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t10",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Return JSON",
                "response_format": "json",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        parsed = json.loads(result.outputs["response_content"])
        assert parsed == {"key": "value"}

    def test_json_format_invalid_json_passthrough(self, logger, monitor):
        provider = _mock_provider()
        provider.chat_completion.return_value = _mock_completion_response(
            content="not valid json"
        )
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t11",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Return JSON",
                "response_format": "json",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_content"] == "not valid json"


class TestErrorHandling:
    def test_missing_api_key(self, logger, monitor):
        factory = MagicMock(spec=AiProviderFactory)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t12",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "user_prompt": "Hello",
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "'api_key' is required" in result.error

    def test_missing_model(self, logger, monitor):
        factory = MagicMock(spec=AiProviderFactory)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t13",
            action="agent",
            inputs={
                "provider": "openai",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "'model' is required" in result.error

    def test_missing_user_prompt(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t14",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "'user_prompt' is required" in result.error

    def test_provider_exception(self, logger, monitor):
        provider = _mock_provider()
        provider.chat_completion.side_effect = RuntimeError("Provider exploded")
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t15",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "Provider exploded" in result.error
        assert result.duration_ms > 0

    def test_unsupported_provider(self, logger, monitor):
        factory = AiProviderFactory(provider_registry={})

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t16",
            action="agent",
            inputs={
                "provider": "unknown_provider",
                "model": "some-model",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "Unsupported AI provider" in result.error


class TestMetrics:
    def test_success_metrics_tracked(self, logger, monitor):
        provider = _mock_provider()
        factory = _mock_factory(provider)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t17",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "credential_ref": "c1", "__credentials": {"c1": {"type": "api_key", "config": {"value": "sk-test"}}},
                "user_prompt": "Hello",
            },
        ))

        metric_names = [m[0] for m in monitor.tracked]
        assert "agent_task_requests" in metric_names
        assert "agent_task_duration_ms" in metric_names
        assert "agent_task_success" in metric_names
        assert "agent_task_total_tokens" in metric_names

    def test_error_metrics_tracked(self, logger, monitor):
        factory = MagicMock(spec=AiProviderFactory)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        uc.execute(ExecuteTaskCommand(
            task_id="t18",
            action="agent",
            inputs={
                "provider": "openai",
                "model": "gpt-4o",
                "user_prompt": "Hello",
            },
        ))

        metric_names = [m[0] for m in monitor.tracked]
        assert "agent_task_requests" in metric_names
        assert "agent_task_error" in metric_names


class TestSchemas:
    def test_input_schema_is_list(self):
        assert isinstance(AGENT_INPUT_SCHEMA, list)
        assert len(AGENT_INPUT_SCHEMA) > 0

    def test_output_schema_is_list(self):
        assert isinstance(AGENT_OUTPUT_SCHEMA, list)
        assert len(AGENT_OUTPUT_SCHEMA) > 0

    def test_input_schema_required_keys(self):
        keys = [field["key"] for field in AGENT_INPUT_SCHEMA]
        assert "provider" in keys
        assert "model" in keys
        assert "api_key" not in keys  # T5 hard-remove: inline secret field is gone
        assert "user_prompt" in keys
        assert "system_prompt" in keys
        assert "temperature" in keys
        assert "max_tokens" in keys
        assert "top_p" in keys
        assert "response_format" in keys
        assert "prompt_variables" in keys
        assert "conversation_history" in keys

    def test_output_schema_required_keys(self):
        keys = [field["key"] for field in AGENT_OUTPUT_SCHEMA]
        assert "response_content" in keys
        assert "model_used" in keys
        assert "finish_reason" in keys
        assert "prompt_tokens" in keys
        assert "completion_tokens" in keys
        assert "total_tokens" in keys
        assert "provider_used" in keys
        assert "elapsed_ms" in keys


class TestProviderFactory:
    def test_factory_create_known_provider(self):
        mock_cls = MagicMock()
        factory = AiProviderFactory(provider_registry={"openai": mock_cls})

        result = factory.create("openai", "sk-test")

        mock_cls.assert_called_once_with(api_key="sk-test")
        assert result == mock_cls.return_value

    def test_factory_create_unknown_provider_raises(self):
        factory = AiProviderFactory(provider_registry={})

        with pytest.raises(ValueError, match="Unsupported AI provider"):
            factory.create("unknown", "key")
