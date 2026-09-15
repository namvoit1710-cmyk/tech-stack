"""SA-1078 — Array Value Transform engine.

Joins TWO arrays of ``{field, value}`` by a match key and transforms the values
of the first array using the second. Pure, in-process logic (no I/O). Distinct
from ``DefaultMappingEngine`` (single-array reshape) — this is a two-array
match/merge.

Locked semantics (defaults; per-field override supported for ``mode``):

* match_by: ``"field"`` (default) — pair items with the same ``field``;
  ``"value"`` — pair items with the same ``value``. First match wins on a
  duplicate key. Comparison is strict (no type coercion).
* mode:
    * ``"replace"`` — array2's value overwrites array1's (status ``replaced``;
      ``unchanged`` if already equal).
    * ``"enrich"`` — keep the original AND add array2's, as
      ``{"original": old, "enriched": new}`` (status ``enriched``).
    * ``"merge"`` — dict+dict → deep-merge (array2 wins on key clash);
      list+list → concat; otherwise → replace (status ``merged``).
* unmatched (array1-only): ``"keep"`` (default, status ``unmatched``) |
  ``"drop"`` (omit from result, still recorded in changes).
* extra (array2-only): ``"ignore"`` (default — recorded in changes, NOT added
  to result) | ``"add"`` (appended to result). status ``extra``.

Output: ``{"result": [...], "changes": [{field, old, new, status}], "summary": {...}}``.
``result`` preserves array1 order; appended extras (if ``extra="add"``) follow.
"""
from __future__ import annotations

from typing import Any


_MISSING = object()


class ArrayTransformEngine:
    VALID_MATCH_BY = ("field", "value")
    VALID_MODES = ("replace", "enrich", "merge")
    VALID_UNMATCHED = ("keep", "drop")
    VALID_EXTRA = ("ignore", "add")

    def transform(
        self,
        array1: list[dict[str, Any]],
        array2: list[dict[str, Any]],
        *,
        match_by: str = "field",
        mode: str = "replace",
        field_overrides: dict[str, str] | None = None,
        unmatched: str = "keep",
        extra: str = "ignore",
    ) -> dict[str, Any]:
        if match_by not in self.VALID_MATCH_BY:
            raise ValueError(f"match_by must be one of {self.VALID_MATCH_BY}, got {match_by!r}")
        if mode not in self.VALID_MODES:
            raise ValueError(f"mode must be one of {self.VALID_MODES}, got {mode!r}")
        if unmatched not in self.VALID_UNMATCHED:
            raise ValueError(f"unmatched must be one of {self.VALID_UNMATCHED}, got {unmatched!r}")
        if extra not in self.VALID_EXTRA:
            raise ValueError(f"extra must be one of {self.VALID_EXTRA}, got {extra!r}")
        overrides = field_overrides or {}

        # Index array2 by match key (first match wins).
        index: dict[Any, dict[str, Any]] = {}
        for item in array2:
            key = self._key(item, match_by)
            if key is _MISSING:
                continue
            hkey = self._hashable(key)
            if hkey not in index:
                index[hkey] = item

        result: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        matched_keys: set[Any] = set()
        counts = {"matched": 0, "unchanged": 0, "unmatched": 0, "extra": 0}

        for item in array1:
            field = item.get("field")
            old = item.get("value")
            key = self._key(item, match_by)
            hkey = self._hashable(key) if key is not _MISSING else _MISSING
            match = index.get(hkey) if hkey is not _MISSING else None

            if match is None:
                # array1-only
                counts["unmatched"] += 1
                changes.append({"field": field, "old": old, "new": old, "status": "unmatched"})
                if unmatched == "keep":
                    result.append({"field": field, "value": old})
                continue

            matched_keys.add(hkey)
            eff_mode = overrides.get(field, mode)
            if eff_mode not in self.VALID_MODES:
                eff_mode = mode
            new, status = self._apply(eff_mode, old, match.get("value"))
            counts["matched"] += 1
            if status == "unchanged":
                counts["unchanged"] += 1
            result.append({"field": field, "value": new})
            changes.append({"field": field, "old": old, "new": new, "status": status})

        # array2-only (extras)
        for item in array2:
            key = self._key(item, match_by)
            hkey = self._hashable(key) if key is not _MISSING else _MISSING
            if hkey is _MISSING or hkey in matched_keys:
                continue
            counts["extra"] += 1
            changes.append({"field": item.get("field"), "old": _MISSING_REPR, "new": item.get("value"), "status": "extra"})
            if extra == "add":
                result.append({"field": item.get("field"), "value": item.get("value")})

        summary = {
            "total_array1": len(array1),
            "total_array2": len(array2),
            "matched": counts["matched"],
            "unchanged": counts["unchanged"],
            "unmatched": counts["unmatched"],
            "extra": counts["extra"],
        }
        return {"result": result, "changes": changes, "summary": summary}

    @staticmethod
    def _key(item: dict[str, Any], match_by: str) -> Any:
        if not isinstance(item, dict):
            return _MISSING
        val = item.get(match_by, _MISSING)
        return val

    @staticmethod
    def _hashable(key: Any) -> Any:
        try:
            hash(key)
            return key
        except TypeError:
            # Non-hashable (dict/list) match value — use a stable repr.
            import json
            return "\x00json\x00" + json.dumps(key, sort_keys=True, default=str)

    def _apply(self, mode: str, old: Any, new: Any) -> tuple[Any, str]:
        if mode == "replace":
            if old == new:
                return old, "unchanged"
            return new, "replaced"
        if mode == "enrich":
            return {"original": old, "enriched": new}, "enriched"
        if mode == "merge":
            return self._merge(old, new), "merged"
        return new, "replaced"

    def _merge(self, old: Any, new: Any) -> Any:
        if isinstance(old, dict) and isinstance(new, dict):
            out = dict(old)
            for k, v in new.items():
                if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                    out[k] = self._merge(out[k], v)
                else:
                    out[k] = v
            return out
        if isinstance(old, list) and isinstance(new, list):
            return old + new
        return new


# Sentinel repr for "old value absent" (extra rows have no array1 counterpart).
_MISSING_REPR = None
