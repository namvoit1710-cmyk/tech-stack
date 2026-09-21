from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class NextStepKind(str, Enum):
    """Category of a post-completion next-step suggestion."""

    FOLLOW_UP = "follow_up"
    WORKFLOW = "workflow"
    AGENT = "agent"
    INFO = "info"


_MAX_TITLE_LEN = 200
_MAX_PROMPT_LEN = 2000


@dataclass
class NextStepSuggestion:
    """A single actionable next-step hint shown after a task completes.

    ``title`` is the short label the UI shows as a clickable hint. ``prompt`` is
    the follow-up message the UI re-sends as a new user message when the hint is
    selected, so the user can trigger the follow-up action. ``kind`` classifies
    the suggestion (a follow-up task, a related workflow or agent, or extra info).
    ``suggestion_id`` is a stable UUID assigned at parse time so the frontend can
    correlate a triggered follow-up back to the originating suggestion.
    """

    title: str
    prompt: str
    kind: NextStepKind = NextStepKind.FOLLOW_UP
    suggestion_id: str = field(default_factory=lambda: "")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "NextStepSuggestion":
        title: object = data.get("title", "")
        prompt: object = data.get("prompt", "")
        if not isinstance(title, str) or not isinstance(prompt, str):
            raise ValueError(
                "NextStepSuggestion requires string 'title' and 'prompt'"
            )
        clean_title: str = title.strip()
        clean_prompt: str = prompt.strip()
        if not clean_title or not clean_prompt:
            raise ValueError(
                "NextStepSuggestion requires non-empty 'title' and 'prompt'"
            )
        if len(clean_title) > _MAX_TITLE_LEN:
            raise ValueError(
                f"NextStepSuggestion title exceeds {_MAX_TITLE_LEN} characters"
            )
        if len(clean_prompt) > _MAX_PROMPT_LEN:
            raise ValueError(
                f"NextStepSuggestion prompt exceeds {_MAX_PROMPT_LEN} characters"
            )
        return cls(
            title=clean_title,
            prompt=clean_prompt,
            kind=cls._parse_kind(data.get("kind")),
            suggestion_id=str(uuid.uuid4()),
        )

    @classmethod
    def _parse_kind(cls, value: object) -> NextStepKind:
        if isinstance(value, NextStepKind):
            return value
        if isinstance(value, str):
            try:
                return NextStepKind(value.strip().lower())
            except ValueError:
                return NextStepKind.FOLLOW_UP
        return NextStepKind.FOLLOW_UP
