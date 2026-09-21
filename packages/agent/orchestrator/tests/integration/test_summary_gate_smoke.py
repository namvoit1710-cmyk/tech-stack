"""Real-LLM behavioral smoke test for the summarize gate (SA-1503).

Unit tests fake the LLM verdict, so they prove the gate's *machinery* but not that the real
model + prompt classify the ticket's phrasings correctly. This test closes that gap: it runs
the actual OpenAI provider against the exact reproduction cases from SA-1503.

It is SKIPPED unless ``OPENAI_API_KEY`` is set, so ordinary/offline runs stay green. Run it as
the behavioral acceptance (rung 65537) step:

    OPENAI_API_KEY=sk-... python -m pytest tests/integration/test_summary_gate_smoke.py -m integration -q
"""

import os

import pytest

from app.layer1_domain.entities.classifier.conversation_summary_gate import ConversationSummaryGate
from app.layer4_frameworks.ai.openai_llm_provider import OpenAILLMProvider

pytestmark = pytest.mark.integration

_NO_KEY = not os.getenv("OPENAI_API_KEY")


class _StdoutLogger:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def gate_and_provider():
    provider = OpenAILLMProvider(api_key=os.environ["OPENAI_API_KEY"])
    return ConversationSummaryGate(), provider, _StdoutLogger()


# Exact reproduction phrasings from the ticket + the languages we discussed.
CONVERSATION_SUMMARY_CASES = [
    "brief the conversation",
    "recap what we just covered",
    "give me the gist",
    "catch me up",
    "what's the takeaway",
    "会話を要約して",                    # Japanese
    "summarize nội dung conversation",  # mixed EN-VI
    "tóm tắt cuộc trò chuyện",
]

BUSINESS_CASES = [
    "summarize this uploaded file",
    "create a material master record",
    "approve the invoice",
]


@pytest.mark.skipif(_NO_KEY, reason="OPENAI_API_KEY not set")
@pytest.mark.parametrize("message", CONVERSATION_SUMMARY_CASES)
def test_real_llm_routes_conversation_summaries(gate_and_provider, message):
    gate, provider, logger = gate_and_provider
    assert gate.is_summarize_request(message, llm_provider=provider, logger=logger) is True


@pytest.mark.skipif(_NO_KEY, reason="OPENAI_API_KEY not set")
@pytest.mark.parametrize("message", BUSINESS_CASES)
def test_real_llm_routes_business(gate_and_provider, message):
    gate, provider, logger = gate_and_provider
    assert gate.is_summarize_request(message, llm_provider=provider, logger=logger) is False
