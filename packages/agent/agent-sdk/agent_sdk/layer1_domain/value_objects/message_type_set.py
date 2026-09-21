from __future__ import annotations

from typing import Any


class MessageTypeSet:
    @staticmethod
    def coerce(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            return {item.strip() for item in value.split(",") if item.strip()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()
