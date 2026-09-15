"""``Comparison`` — type-flexible value comparison for CONDITION / SWITCH rules.

Both handlers evaluate structured rules ``{key, operations, value}`` against a
runtime context. This value object is the single source of truth for operator
semantics — add new operators here, not in the handlers.

Comparison rules:
  * Operator names are case-insensitive (``"=="``, ``"Equal"``, ``"EQUALS"``).
  * Both sides are independently coerced to ``float`` when possible, so
    ``"10" > "9"`` evaluates numerically rather than lexicographically.
  * ``bool`` maps to ``1.0`` / ``0.0`` (``float(str(True))`` would raise).
  * Equality falls back to lower-cased string compare when numeric coercion
    isn't possible (so ``"YES" == "yes"`` is True).
  * Unknown operators return False.
"""

from __future__ import annotations

import re
from typing import Any


class Comparison:
    """Evaluates a comparison operator over two values."""

    @staticmethod
    def evaluate(left: Any, op: str, right: Any) -> bool:
        """Compare two values using ``op``. See the class docstring for rules."""
        op = (op or "").lower()

        left_str = str(left).lower() if left is not None else ""
        right_str = str(right).lower() if right is not None else ""

        left_num: float | None = None
        right_num: float | None = None
        try:
            if isinstance(left, bool):
                left_num = 1.0 if left else 0.0
            elif left is not None:
                left_num = float(str(left))
        except (ValueError, TypeError):
            pass
        try:
            if isinstance(right, bool):
                right_num = 1.0 if right else 0.0
            elif right is not None:
                right_num = float(str(right))
        except (ValueError, TypeError):
            pass

        if op in ("=", "==", "equals", "equal"):
            if left_num is not None and right_num is not None:
                return left_num == right_num
            return left_str == right_str
        if op in ("!=", "not_equals", "not_equal"):
            if left_num is not None and right_num is not None:
                return left_num != right_num
            return left_str != right_str
        if op in (">", "gt", "greater_than"):
            if left_num is not None and right_num is not None:
                return left_num > right_num
            return left_str > right_str
        if op in (">=", "gte", "greater_or_equal"):
            if left_num is not None and right_num is not None:
                return left_num >= right_num
            return left_str >= right_str
        if op in ("<", "lt", "less_than"):
            if left_num is not None and right_num is not None:
                return left_num < right_num
            return left_str < right_str
        if op in ("<=", "lte", "less_or_equal"):
            if left_num is not None and right_num is not None:
                return left_num <= right_num
            return left_str <= right_str
        if op == "contains":
            return str(right) in str(left)
        if op == "not_contains":
            return str(right) not in str(left)
        if op == "starts_with":
            return str(left).startswith(str(right))
        if op == "ends_with":
            return str(left).endswith(str(right))
        if op == "is_empty":
            return left is None or left == "" or left == [] or left == {}
        if op == "is_not_empty":
            return not (left is None or left == "" or left == [] or left == {})
        if op == "in":
            if isinstance(right, str):
                return str(left) in right
            if isinstance(right, (list, tuple, set)):
                if left in right:
                    return True
                # also match by stringified form (rules from frontend often arrive as strings)
                return str(left) in {str(x) for x in right}
            return False
        if op == "not_in":
            if isinstance(right, str):
                return str(left) not in right
            if isinstance(right, (list, tuple, set)):
                if left in right:
                    return False
                return str(left) not in {str(x) for x in right}
            return True
        if op == "regex":
            try:
                return bool(re.search(str(right), str(left)))
            except re.error:
                return False
        return False
