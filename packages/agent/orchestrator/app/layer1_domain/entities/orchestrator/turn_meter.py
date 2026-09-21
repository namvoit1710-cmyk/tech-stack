from __future__ import annotations

import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s"


@dataclass
class TurnMeter:
    """Per-turn token/latency accumulator, shared by every LLM call within one turn."""

    message_id: str
    conv_id: str
    start_perf: float
    total_tokens: Optional[int] = None

    @classmethod
    def start_now(cls, *, message_id: str, conv_id: str) -> "TurnMeter":
        return cls(message_id=message_id, conv_id=conv_id, start_perf=time.perf_counter())

    def record_call(self, tokens: Optional[int]) -> None:
        if tokens is None:
            return
        self.total_tokens = (self.total_tokens or 0) + tokens

    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.start_perf

    def render(self, *, done: bool) -> str:
        duration: str = _format_duration(self.elapsed_seconds())
        tokens_part: str
        if self.total_tokens is None:
            tokens_part = ""
        elif done:
            tokens_part = f", {self.total_tokens:,} tokens"
        else:
            tokens_part = f" · {self.total_tokens:,} tokens"
        return f"✻ Cooked for {duration}{tokens_part}" if done else f"Cooking… {duration}{tokens_part}"


_turn_meter: ContextVar[Optional[TurnMeter]] = ContextVar("orchestrator_turn_meter", default=None)


def get_turn_meter() -> Optional[TurnMeter]:
    return _turn_meter.get()


def start_turn(meter: TurnMeter) -> Token:
    return _turn_meter.set(meter)


def reset_turn(token: Token) -> None:
    _turn_meter.reset(token)
