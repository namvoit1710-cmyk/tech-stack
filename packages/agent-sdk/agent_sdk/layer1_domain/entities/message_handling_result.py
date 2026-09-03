from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

MessageAction = Literal["ack", "nack", "reject", "none"]


@dataclass(frozen=True, slots=True)
class MessageHandlingResult:
    action: MessageAction = "ack"
    requeue: bool = False
    reason: str = ""

    @classmethod
    def ack(cls, reason: str = "") -> "MessageHandlingResult":
        return cls(action="ack", requeue=False, reason=reason)

    @classmethod
    def nack(
        cls,
        *,
        requeue: bool = True,
        reason: str = "",
    ) -> "MessageHandlingResult":
        return cls(action="nack", requeue=requeue, reason=reason)

    @classmethod
    def reject(cls, reason: str = "") -> "MessageHandlingResult":
        return cls(action="reject", requeue=False, reason=reason)

    @classmethod
    def none(cls, reason: str = "") -> "MessageHandlingResult":
        """Leave delivery settlement to the handler.

        Routers intentionally do not ack/nack/reject this result. Use it only
        when the custom handler has settled, or will settle, the delivery itself.
        """
        return cls(action="none", requeue=False, reason=reason)

    @classmethod
    def from_value(cls, value: Any) -> "MessageHandlingResult":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.ack()
        if isinstance(value, str):
            action = value.lower().strip()
            if action in {"ack", "nack", "reject", "none"}:
                return cls(
                    action=cast(MessageAction, action), requeue=(action == "nack")
                )
            return cls.reject(reason=f"Unsupported handler result string: {value}")
        if isinstance(value, dict):
            action = str(value.get("action", "ack")).lower().strip()
            if action not in {"ack", "nack", "reject", "none"}:
                return cls.reject(reason=f"Unsupported handler result action: {action}")
            return cls(
                action=cast(MessageAction, action),
                requeue=bool(value.get("requeue", action == "nack")),
                reason=str(value.get("reason", "")),
            )
        return cls.reject(
            reason=f"Unsupported handler result type: {type(value).__name__}"
        )
