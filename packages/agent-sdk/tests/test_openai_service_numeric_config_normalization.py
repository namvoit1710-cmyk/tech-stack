from __future__ import annotations

import pytest

from agent_sdk.layer4_frameworks.ai.openai_service import (
    LangChainLLMService,
    _coerce_optional_float,
    _coerce_optional_int,
)


def test_coerce_optional_float_accepts_string_timeout() -> None:
    assert _coerce_optional_float("180.0", field_name="timeout") == 180.0


def test_langchain_llm_service_coerces_timeout_string() -> None:
    service = LangChainLLMService(api_key="x", timeout="180.0")
    assert service.timeout == 180.0


def test_langchain_llm_service_coerces_temperature_and_retry_strings() -> None:
    service = LangChainLLMService(
        api_key="x",
        temperature="0.01",
        max_tokens="1024",
        max_retries="2",
    )
    assert service.temperature == 0.01
    assert service.max_tokens == 1024
    assert service.max_retries == 2


@pytest.mark.parametrize("value", [None, "", "   "])
def test_coerce_optional_float_allows_missing_values(value: object) -> None:
    assert _coerce_optional_float(value, field_name="timeout") is None


@pytest.mark.parametrize("value", ["abc", True])
def test_coerce_optional_float_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        _coerce_optional_float(value, field_name="timeout")


@pytest.mark.parametrize("value", ["3", "3.0", 3, 3.0])
def test_coerce_optional_int_accepts_integer_values(value: object) -> None:
    assert _coerce_optional_int(value, field_name="max_retries") == 3


@pytest.mark.parametrize("value", ["3.5", 3.5, True, "abc"])
def test_coerce_optional_int_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        _coerce_optional_int(value, field_name="max_retries")
