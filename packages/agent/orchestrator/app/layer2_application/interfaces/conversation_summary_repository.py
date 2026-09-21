from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ConversationTitleSource(str, Enum):
    STRATEGY = "strategy_context_summary"
    USER_MESSAGE = "user_message_fallback"
    # SA-2168: an explicit user /rename — highest precedence, must survive later auto-titling.
    USER_RENAME = "user_rename"


@dataclass(slots=True)
class ConversationTitleSummary:
    conv_id: str
    title: str
    source: ConversationTitleSource


class IConversationSummaryRepository(Protocol):
    def save_title(self, summary: ConversationTitleSummary) -> None: ...
    def get_title(self, conv_id: str) -> ConversationTitleSummary | None: ...
    def get_titles(self, conv_ids: list[str]) -> dict[str, ConversationTitleSummary]: ...
    # SA-2168: unconditional set (source USER_RENAME) — never preserved-over by save_title.
    def update_title(self, conv_id: str, title: str) -> None: ...
