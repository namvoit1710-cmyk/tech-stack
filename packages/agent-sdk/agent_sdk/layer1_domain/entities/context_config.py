from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CompactionStrategy(str, Enum):
    NONE = "none"
    HEAD_TAIL = "head_tail"
    TOOL_RESULT_CLEAR = "tool_result_clear"
    SELECTIVE = "selective"
    TIERED = "tiered"


class PayloadType(str, Enum):
    NARRATIVE = "narrative"
    BUSINESS_DATA = "business_data"
    ROUTING = "routing"


@dataclass
class ContextBudgetConfig:
    max_total_tokens: int = 16_000
    max_message_history: int = 10
    max_tool_result_tokens: int = 2_000
    compaction_strategy: CompactionStrategy = CompactionStrategy.SELECTIVE
    compaction_threshold: float = 0.8
    preserve_business_payloads: bool = True
    aggressive_threshold: float = 0.85
    danger_threshold: float = 0.95
