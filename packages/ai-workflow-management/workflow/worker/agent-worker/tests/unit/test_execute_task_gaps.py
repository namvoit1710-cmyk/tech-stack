"""Gap-fill unit specs for ExecuteTaskUseCase.

Authored by unit-smith (white-box). The sibling test_execute_task.py leaves these
branches of execute_task_usecase.py uncovered (baseline 92%, missing 209/211/213 and
282-292):

- Parameter-range validation guards (source lines 208-213): temperature/max_tokens/top_p
  out of range each raise ValueError which the outer try/except maps to status=ERROR.
- The inline ``_resolve_provider`` fallback used when NO factory is injected
  (source lines 281-292): imports OpenAiProvider, builds a registry, constructs the
  provider by name, and raises "Unsupported AI provider" for an unknown name.
- resolve_effective_auth edge: a non-api_key credential type yields empty api_key ->
  "'api_key' is required." (source lines 185-190).

Mock kind: ExecuteTaskUseCase.execute and IAiProvider.chat_completion are SYNC, so
collaborators are MagicMock (per MOCK_KIND_DRIFT invariant). The DummyLogger/DummyMonitor
fixtures come from tests/conftest.py.
"""

from unittest.mock import MagicMock, patch

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    AiProviderFactory,
)
from app.layer1_domain.entities.ai_completion_response import AiCompletionResponse
from app.layer1_domain.entities.ai_usage import AiUsage


_CRED = {"c1": {"type": "api_key", "config": {"value": "sk-test"}}}


def _mock_completion_response() -> AiCompletionResponse:
    return AiCompletionResponse(
        content="ok",
        model="gpt-4o",
        usage=AiUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        finish_reason="stop",
    )


def _mock_factory() -> AiProviderFactory:
    provider = MagicMock()
    provider.chat_completion.return_value = _mock_completion_response()
    factory = MagicMock(spec=AiProviderFactory)
    factory.create.return_value = provider
    return factory


def _base_inputs(**overrides):
    inputs = {
        "provider": "openai",
        "model": "gpt-4o",
        "credential_ref": "c1",
        "__credentials": _CRED,
        "user_prompt": "Hello",
    }
    inputs.update(overrides)
    return inputs


# ── Parameter-range validation (invalid input + boundary) ────────────────────


class TestParameterRangeValidation:
    # invalid input — temperature above 2.0 (source line 208-209)
    def test_temperature_too_high(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g1", action="agent", inputs=_base_inputs(temperature=2.5),
        ))
        assert result.status == TaskStatus.ERROR
        assert "temperature must be between 0.0 and 2.0" in result.error

    # invalid input — temperature below 0.0
    def test_temperature_negative(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g2", action="agent", inputs=_base_inputs(temperature=-0.1),
        ))
        assert result.status == TaskStatus.ERROR
        assert "temperature must be between 0.0 and 2.0" in result.error

    # invalid input — max_tokens over the 128000 ceiling (source line 210-211)
    def test_max_tokens_too_high(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g3", action="agent", inputs=_base_inputs(max_tokens=200000),
        ))
        assert result.status == TaskStatus.ERROR
        assert "max_tokens must be between 1 and 128000" in result.error

    # boundary — max_tokens of 0 is below the floor of 1
    def test_max_tokens_zero(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g4", action="agent", inputs=_base_inputs(max_tokens=0),
        ))
        assert result.status == TaskStatus.ERROR
        assert "max_tokens must be between 1 and 128000" in result.error

    # invalid input — top_p above 1.0 (source line 212-213)
    def test_top_p_too_high(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g5", action="agent", inputs=_base_inputs(top_p=1.5),
        ))
        assert result.status == TaskStatus.ERROR
        assert "top_p must be between 0.0 and 1.0" in result.error

    # boundary — exact edges (2.0 / 128000 / 1.0) are INCLUSIVE => success (source uses <=)
    def test_boundary_values_accepted(self, logger, monitor):
        factory = _mock_factory()
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=factory)
        result = uc.execute(ExecuteTaskCommand(
            task_id="g6", action="agent",
            inputs=_base_inputs(temperature=2.0, max_tokens=128000, top_p=1.0),
        ))
        assert result.status == TaskStatus.SUCCESS


# ── Inline _resolve_provider fallback (no factory injected) ───────────────────


class TestInlineProviderFallback:
    # happy path — no factory: use-case builds an OpenAiProvider itself (source line 281-292).
    # Patch the OpenAI httpx seam so no live call is made; assert the response flows through.
    @patch("app.layer4_frameworks.providers.openai_provider.httpx.Client")
    def test_inline_openai_fallback(self, mock_client_cls, logger, monitor):
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "inline-ok"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        mock_client.post.return_value = resp
        mock_client_cls.return_value = mock_client

        # ai_provider_factory omitted entirely => _resolve_provider takes the inline branch
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="g7", action="agent", inputs=_base_inputs(),
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_content"] == "inline-ok"

    # failure path — no factory + unknown provider name => "Unsupported AI provider"
    # (source line 288-290, the inline branch's own guard)
    def test_inline_unknown_provider(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="g8", action="agent", inputs=_base_inputs(provider="not-a-provider"),
        ))
        assert result.status == TaskStatus.ERROR
        assert "Unsupported AI provider" in result.error


# ── resolve_effective_auth edge: wrong credential type ────────────────────────


class TestCredentialTypeEdge:
    # edge/invalid input — a non-api_key credential type yields empty api_key,
    # so the "'api_key' is required." guard fires (source line 185-190).
    def test_non_api_key_credential_type_rejected(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g9", action="agent",
            inputs=_base_inputs(__credentials={"c1": {"type": "bearer", "config": {"token": "t"}}}),
        ))
        assert result.status == TaskStatus.ERROR
        assert "'api_key' is required" in result.error

    # boundary — api_key credential present but value is whitespace-only => stripped to ""
    # -> "'api_key' is required." (source line 186 .strip(), line 189 guard)
    def test_whitespace_api_key_rejected(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, ai_provider_factory=_mock_factory())
        result = uc.execute(ExecuteTaskCommand(
            task_id="g10", action="agent",
            inputs=_base_inputs(__credentials={"c1": {"type": "api_key", "config": {"value": "   "}}}),
        ))
        assert result.status == TaskStatus.ERROR
        assert "'api_key' is required" in result.error
