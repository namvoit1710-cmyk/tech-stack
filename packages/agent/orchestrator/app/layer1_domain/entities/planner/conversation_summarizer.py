"""Summarizes a conversation from its message history (SA-1308).

Pure domain: given the conversation turns, it builds the transcript, asks the LLM provider
(an L1 interface) for a structured summary, and renders it for the user. This is the piece
that finally *reads the historical messages* — the gap the ticket calls out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer1_domain.interfaces.logger_interface import ILogger
from app.layer1_domain.prompts.build_summary_prompt import build_summary_prompt


@dataclass
class ConversationTurn:
    """One message in the conversation, normalized for summarization."""
    role: str
    content: str


@dataclass
class ConversationSummaryResponse:
    """Structured summary the LLM returns (matches the ticket's three asks)."""
    summary: str = ""
    what_the_system_did: str = ""
    recommended_next_steps: list[str] = field(default_factory=list)


class ConversationSummarizer:
    """Builds a transcript from turns and produces a user-facing summary."""

    EMPTY_MESSAGE = (
        "There isn't any conversation to summarize yet. Send a request first and I'll "
        "be able to recap what we did."
    )

    @staticmethod
    def build_transcript(turns: list[ConversationTurn]) -> str:
        lines: list[str] = []
        for turn in turns:
            content = (turn.content or "").strip()
            if not content:
                continue
            role = (turn.role or "user").strip().lower()
            speaker = "User" if role == "user" else "Assistant"
            lines.append(f"{speaker}: {content}")
        return "\n".join(lines)

    def summarize(
        self,
        *,
        turns: list[ConversationTurn],
        latest_request: str,
        llm_provider: ILLMProvider,
        logger: ILogger,
    ) -> ConversationSummaryResponse | None:
        """Return a structured summary, or ``None`` when there is nothing to summarize."""
        transcript = self.build_transcript(turns)
        if not transcript:
            logger.info("No conversation history available to summarize")
            return None

        messages = build_summary_prompt(transcript=transcript, latest_request=latest_request)
        summary = llm_provider.complete_with_format(
            messages=messages,
            response_model=ConversationSummaryResponse,
        )
        logger.info("Conversation summary generated", summary=summary)
        return summary

    @classmethod
    def render(cls, summary: ConversationSummaryResponse | None) -> str:
        """Render the structured summary as user-facing markdown."""
        if summary is None:
            return cls.EMPTY_MESSAGE

        sections: list[str] = []
        overview = (summary.summary or "").strip()
        if overview:
            sections.append(f"**Summary**\n\n{overview}")

        did = (summary.what_the_system_did or "").strip()
        if did:
            sections.append(f"**What the system did**\n\n{did}")

        steps = [step.strip() for step in (summary.recommended_next_steps or []) if step and step.strip()]
        if steps:
            bullet_list = "\n".join(f"- {step}" for step in steps)
            sections.append(f"**Recommended next steps**\n\n{bullet_list}")

        if not sections:
            return (
                "Here's a recap of our conversation, but there wasn't enough detail to "
                "summarize meaningfully yet."
            )
        return "\n\n".join(sections)
