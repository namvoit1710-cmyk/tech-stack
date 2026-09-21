"""Unit tests for the Stage-0 ConversationSummaryGate (SA-1503).

These tests verify the gate's *machinery* — routing on the LLM verdict, caching, fail-closed
behavior, normalization, and empty-input handling. Whether the LLM itself correctly classifies
Japanese or "give me the gist" is the model's job (behavioral/integration concern), not a unit
test — so the LLM verdict is faked here.
"""

from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.classifier.conversation_summary_gate import (
    ConversationSummaryGate,
    GateDecision,
    SummaryIntent,
)


class FakeProvider:
    """Records calls and returns a scripted GateDecision (or raises)."""

    def __init__(self, decision=None, raises: Exception | None = None):
        self._decision = decision
        self._raises = raises
        self.calls = 0

    def complete_with_format(self, *, messages, response_model):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._decision


@pytest.fixture
def gate():
    return ConversationSummaryGate()


@pytest.fixture
def logger():
    return MagicMock()


# --- routing on the LLM verdict -------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "brief the conversation",
        "give me the gist",
        "catch me up",
        "会話を要約して",                 # Japanese
        "summarize nội dung conversation",  # mixed EN-VI
        "tóm tắt lại giúp tôi",
    ],
)
def test_routes_true_when_llm_says_conversation_summary(gate, logger, message):
    provider = FakeProvider(GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value))
    assert gate.is_summarize_request(message, llm_provider=provider, logger=logger) is True
    assert provider.calls == 1


@pytest.mark.parametrize(
    "message",
    [
        "summarize this uploaded file",
        "create a material master record",
        "summarize the data in the table and save it",
    ],
)
def test_routes_false_when_llm_says_business(gate, logger, message):
    provider = FakeProvider(GateDecision(intent=SummaryIntent.BUSINESS.value))
    assert gate.is_summarize_request(message, llm_provider=provider, logger=logger) is False
    assert provider.calls == 1


# --- cache (Cơ chế 1): repeat phrasings replay at 0 LLM ------------------------

def test_cache_avoids_second_llm_call(gate, logger):
    provider = FakeProvider(GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value))
    m = "brief the conversation"
    assert gate.is_summarize_request(m, llm_provider=provider, logger=logger) is True
    assert gate.is_summarize_request(m, llm_provider=provider, logger=logger) is True
    assert provider.calls == 1  # second call served from cache


def test_normalization_shares_one_cache_entry(gate, logger):
    provider = FakeProvider(GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value))
    assert gate.is_summarize_request("Give me the GIST", llm_provider=provider, logger=logger) is True
    # Different case + trailing/collapsed whitespace -> same normalized key -> no new call.
    assert gate.is_summarize_request("  give me the   gist ", llm_provider=provider, logger=logger) is True
    assert provider.calls == 1


# --- fail-closed (LEC-FALLBACK-BAN) -------------------------------------------

def test_fail_closed_on_llm_error_routes_business(gate, logger):
    provider = FakeProvider(raises=RuntimeError("LLM down"))
    assert gate.is_summarize_request("brief the conversation", llm_provider=provider, logger=logger) is False
    logger.error.assert_called_once()


def test_fail_closed_result_is_not_cached(gate, logger):
    """An error must not poison the cache — a later healthy call must be able to succeed."""
    provider = FakeProvider(raises=RuntimeError("transient"))
    assert gate.is_summarize_request("brief the conversation", llm_provider=provider, logger=logger) is False
    healthy = FakeProvider(GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value))
    assert gate.is_summarize_request("brief the conversation", llm_provider=healthy, logger=logger) is True
    assert healthy.calls == 1


# --- empty / invalid input never hits the LLM ---------------------------------

@pytest.mark.parametrize("message", ["", "   ", None])
def test_empty_input_returns_false_without_calling_llm(gate, logger, message):
    provider = FakeProvider(GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value))
    assert gate.is_summarize_request(message, llm_provider=provider, logger=logger) is False  # type: ignore[arg-type]
    assert provider.calls == 0


def test_missing_intent_field_defaults_to_business(gate, logger):
    """A malformed decision (no CONVERSATION_SUMMARY) must be treated as BUSINESS, not summary."""
    provider = FakeProvider(GateDecision())  # defaults to BUSINESS
    assert gate.is_summarize_request("anything", llm_provider=provider, logger=logger) is False


def test_cache_is_bounded(gate, logger):
    """A long-lived process must not grow the cache without bound."""
    provider = FakeProvider(GateDecision(intent=SummaryIntent.BUSINESS.value))
    for i in range(ConversationSummaryGate._MAX_CACHE_ENTRIES + 50):
        gate.is_summarize_request(f"message number {i}", llm_provider=provider, logger=logger)
    assert len(gate._cache) <= ConversationSummaryGate._MAX_CACHE_ENTRIES
