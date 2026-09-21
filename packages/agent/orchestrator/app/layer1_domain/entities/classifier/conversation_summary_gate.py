"""Stage-0 routing gate: is the user asking to summarize the *conversation itself*? (SA-1503)

SA-1308 introduced a summarize fast-path guarded by ``SummarizeIntentDetector`` — a heuristic
that matched literal substrings from a few hardcoded EN/VI phrase lists. Realistic phrasings
("brief the conversation", "give me the gist"), other languages (Japanese), and mixed EN-VI
slipped through, so the request fell through the planner and the user got "Not supported"
(SA-1503).

This gate replaces that heuristic with an LLM intent classification, which handles the open
set of phrasings and any language. It keeps two safety properties:

* **Cache** — the decision for a normalized message is memoized, so repeat phrasings replay
  at zero LLM cost. ``Cost(n) = discovery(1) + replay(n-1)``.
* **Fail-closed** — on any LLM error the gate returns ``False`` (BUSINESS), i.e. the request
  flows to the planner exactly as it does today. The gate never *fabricates* a summary intent
  when it cannot get a reliable answer (LEC-FALLBACK-BAN: no silent guessing).

The asset-vs-conversation guard that used to live in the heuristic now lives in the prompt:
"summarize this uploaded file" classifies as BUSINESS and still routes to a Business Agent.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer1_domain.interfaces.logger_interface import ILogger
from app.layer1_domain.prompts.build_summary_gate_prompt import build_summary_gate_prompt


class SummaryIntent(str, Enum):
    CONVERSATION_SUMMARY = "CONVERSATION_SUMMARY"
    BUSINESS = "BUSINESS"


@dataclass
class GateDecision:
    """Structured verdict the gate LLM returns."""
    intent: str = SummaryIntent.BUSINESS.value


class ConversationSummaryGate:
    """LLM-backed router: does this message ask to summarize the conversation?

    Replaces the literal-substring ``SummarizeIntentDetector``. Public contract is the same
    boolean the call site expects, so the Orchestrator use case changes by one line.
    """

    # Cap the memo so a long-lived process cannot grow it without bound; oldest entries are
    # evicted first (FIFO on insertion order). A routing verdict is cheap to re-derive, so a
    # modest cap trades a rare re-classification for a fixed memory ceiling.
    _MAX_CACHE_ENTRIES = 2048

    def __init__(self) -> None:
        # Process-local memo: normalized message -> decision (repeat phrasings replay at 0 LLM).
        self._cache: dict[str, bool] = {}

    def _remember(self, key: str, value: bool) -> None:
        if len(self._cache) >= self._MAX_CACHE_ENTRIES:
            # Evict the oldest inserted key (dicts preserve insertion order in 3.7+).
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = value

    @staticmethod
    def _normalize(message: str) -> str:
        """Canonicalize so trivially-different phrasings share one cache entry."""
        text = unicodedata.normalize("NFKC", message or "").strip().lower()
        return " ".join(text.split())

    def is_summarize_request(
        self,
        message: str,
        *,
        llm_provider: ILLMProvider,
        logger: ILogger,
    ) -> bool:
        """Return True iff the message asks to summarize the conversation itself.

        Fail-closed: any LLM error yields False (route to the planner as today). Cached by
        normalized message so repeat phrasings do not re-hit the LLM.
        """
        if not message or not isinstance(message, str):
            return False

        key = self._normalize(message)
        if not key:
            return False

        if key in self._cache:
            return self._cache[key]

        try:
            decision = llm_provider.complete_with_format(
                messages=build_summary_gate_prompt(message),
                response_model=GateDecision,
            )
            intent = str(getattr(decision, "intent", "") or "").strip().upper()
            is_summary = intent == SummaryIntent.CONVERSATION_SUMMARY.value
        except Exception as exc:  # fail-closed — never guess a summarize intent
            logger.error(
                "Summary gate LLM failed; routing BUSINESS (fail-closed)",
                error=str(exc),
            )
            return False

        self._remember(key, is_summary)
        logger.info("Summary gate decision", is_summary=is_summary, intent=intent)
        return is_summary
