"""SA-1815 — Aggregate / Reduce engine (N source items -> grouped rows).

Given a list of item dicts, ``group_by`` (0+ field paths) and ``aggregations``
(each ``{op, field?, target?, separator?}``), produce one output row per group:

* ``group_by`` empty  -> a single overall row (reduce all items).
* ``group_by`` set     -> one row per distinct group-key tuple, first-seen order.

Each row = the group-key fields + one entry per aggregation. Ops:

* ``count``   -> number of items in the group (``field`` ignored).
* ``sum``/``min``/``max``/``avg`` -> over numeric ``item[field]`` (non-numeric /
  missing values are skipped; empty -> ``sum``=0, others -> ``None``).
* ``collect`` -> list of ``item[field]`` values (missing/None skipped).
* ``join``    -> ``separator`` (default ",") joined str() of the collected values.

Pure, in-process (no I/O). Path access supports dot + ``[i]`` like the mapping
engine. Distinct from ``DefaultMappingEngine`` (per-item reshape) and
``ArrayTransformEngine`` (two-array merge) — this is a fan-in reducer.
"""
from __future__ import annotations

import json
import re
from typing import Any

_MISSING = object()
VALID_OPS = ("sum", "count", "min", "max", "avg", "collect", "join")


class AggregateEngine:
    def aggregate(
        self,
        items: list[Any],
        group_by: list[str] | None,
        aggregations: list[dict[str, Any]] | None,
        *,
        strict_mode: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(items, list):
            raise ValueError("aggregate source must be a list")
        group_by = list(group_by or [])
        aggregations = list(aggregations or [])
        errors: list[str] = []

        # -- Partition into groups (first-seen order). No group_by => one group. --
        groups: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        for item in items:
            item = item if isinstance(item, dict) else {"_value": item}
            keyvals: dict[str, Any] = {}
            key_parts: list[Any] = []
            for gb in group_by:
                v = self._nav(item, gb)
                v = None if v is _MISSING else v
                keyvals[gb] = v
                key_parts.append(self._hashable(v))
            k = tuple(key_parts)
            if k not in groups:
                groups[k] = {"keyvals": keyvals, "items": []}
                order.append(k)
            groups[k]["items"].append(item)

        if not group_by:
            all_items = [i if isinstance(i, dict) else {"_value": i} for i in items]
            groups = {(): {"keyvals": {}, "items": all_items}}
            order = [()]

        # -- Reduce each group. --
        rows: list[dict[str, Any]] = []
        for k in order:
            g = groups[k]
            row: dict[str, Any] = dict(g["keyvals"])
            for agg in aggregations:
                op = str(agg.get("op", ""))
                field = agg.get("field")
                target = agg.get("target") or field or op
                try:
                    row[target] = self._apply(op, g["items"], field, agg)
                except Exception as exc:  # record, keep the rest of the row
                    errors.append(f"aggregation '{op}' -> '{target}': {exc}")
                    if strict_mode:
                        # strict still records; row keeps other aggregations
                        pass
            rows.append(row)

        return {"result": rows, "group_count": len(rows), "errors": errors}

    # -- op application -----------------------------------------------------

    def _apply(self, op: str, items: list[dict[str, Any]], field: Any, agg: dict[str, Any]) -> Any:
        if op not in VALID_OPS:
            raise ValueError(f"unknown op '{op}' (valid: {', '.join(VALID_OPS)})")
        if op == "count":
            return len(items)

        values: list[Any] = []
        for it in items:
            v = self._nav(it, field) if field else _MISSING
            if v is _MISSING or v is None:
                continue
            values.append(v)

        if op == "collect":
            return values
        if op == "join":
            separator = agg.get("separator", ",")
            return separator.join(str(v) for v in values)

        # numeric ops
        nums: list[float] = []
        for v in values:
            if isinstance(v, bool):
                continue
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
        if op == "sum":
            return self._as_number(sum(nums))
        if not nums:
            return None
        if op == "min":
            return self._as_number(min(nums))
        if op == "max":
            return self._as_number(max(nums))
        if op == "avg":
            return self._as_number(sum(nums) / len(nums))
        return None

    @staticmethod
    def _as_number(value: float) -> int | float:
        return int(value) if value == int(value) else value

    # -- path nav (dot + [i]); returns _MISSING sentinel on any miss --------

    @staticmethod
    def _nav(data: Any, path: Any) -> Any:
        if not path:
            return _MISSING
        cur: Any = data
        for seg in str(path).split("."):
            for tok in re.split(r"(\[[^\]]*\])", seg):
                tok = tok.strip()
                if not tok:
                    continue
                if tok.startswith("[") and tok.endswith("]"):
                    if not isinstance(cur, list):
                        return _MISSING
                    try:
                        idx = int(tok[1:-1])
                    except ValueError:
                        return _MISSING
                    if idx < 0 or idx >= len(cur):
                        return _MISSING
                    cur = cur[idx]
                else:
                    if not isinstance(cur, dict) or tok not in cur:
                        return _MISSING
                    cur = cur[tok]
        return cur

    @staticmethod
    def _hashable(value: Any) -> Any:
        try:
            hash(value)
            return value
        except TypeError:
            return "\x00json\x00" + json.dumps(value, sort_keys=True, default=str)
