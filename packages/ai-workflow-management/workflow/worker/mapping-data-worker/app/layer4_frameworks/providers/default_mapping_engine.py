import json
import re
from typing import Any

from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer1_domain.value_objects.mapping_field_type import MappingFieldType
from app.layer1_domain.value_objects.mapping_mode import MappingMode
from app.layer4_frameworks.exceptions import FieldAccessError, TypeCoercionError


class _Missing:
    """Sentinel indicating a field was not found."""
    pass


MISSING = _Missing()

# SA-1813 — value-handling policies (see map_data). Defaults derive from
# strict_mode so pre-existing callers are unchanged.
_ON_MISSING = ("skip", "null", "error")
_ON_TYPE_ERROR = ("keep", "null", "error")


class DefaultMappingEngine:
    """IMappingEngine implementation with nested access, type coercion, and array mapping."""

    def map_data(
        self,
        source_data: dict[str, Any],
        mapping_rules: list[MappingRule],
        array_mappings: list[ArrayMappingConfig],
        strict_mode: bool = False,
        on_missing: str | None = None,
        on_type_error: str | None = None,
    ) -> dict[str, Any]:
        mapped_data: dict[str, Any] = {}
        applied_rules_count: int = 0
        skipped_rules: list[str] = []
        errors: list[str] = []

        # SA-1813: 3-way policies. Defaults derive from strict_mode so existing
        # callers stay byte-for-byte the same — missing -> error (strict) / skip
        # (lenient); coercion error -> recorded. on_missing="null" writes null;
        # on_type_error="keep" writes the raw value, "null" writes null.
        eff_on_missing = on_missing if on_missing in _ON_MISSING else ("error" if strict_mode else "skip")
        eff_on_type_error = on_type_error if on_type_error in _ON_TYPE_ERROR else "error"

        # -- Process direct/constant mapping rules --
        for rule in mapping_rules:
            try:
                value: Any = self._resolve_rule_value(rule, source_data)

                if isinstance(value, _Missing):
                    action, out = self._on_missing_action(eff_on_missing, rule, skipped_rules, errors)
                else:
                    try:
                        out = self._coerce_value(value, rule.field_type)
                        action = "write"
                    except (TypeCoercionError, FieldAccessError) as exc:
                        action, out = self._on_type_error_action(eff_on_type_error, rule, value, exc, errors)

                if action == "write":
                    self._set_nested(mapped_data, rule.target_field, out)
                    applied_rules_count += 1

            except Exception as exc:
                errors.append(f"Rule '{rule.source_path}' -> '{rule.target_field}': {exc}")

        # -- Process array mappings --
        for array_cfg in array_mappings:
            try:
                source_array: Any = self._get_nested_value(source_data, array_cfg.source_array_path)

                if isinstance(source_array, _Missing):
                    if strict_mode:
                        errors.append(f"Missing source array: '{array_cfg.source_array_path}'")
                    else:
                        skipped_rules.append(array_cfg.source_array_path)
                    continue

                if not isinstance(source_array, list):
                    errors.append(
                        f"Source path '{array_cfg.source_array_path}' is not an array "
                        f"(got {type(source_array).__name__})"
                    )
                    continue

                # -- "values" mode: pluck a single field into a flat list --
                if array_cfg.output_mode == "values":
                    pluck_path: str = array_cfg.pluck_field or ""
                    if not pluck_path:
                        errors.append(
                            f"Array mapping '{array_cfg.source_array_path}': "
                            f"output_mode='values' requires 'pluck_field' to be set"
                        )
                        continue

                    flat_values: list[Any] = []
                    for idx, item in enumerate(source_array):
                        if not isinstance(item, dict):
                            item = {"_value": item}
                        val = self._get_nested_value(item, pluck_path)
                        if isinstance(val, _Missing):
                            if strict_mode:
                                errors.append(
                                    f"Array '{array_cfg.source_array_path}[{idx}]': "
                                    f"missing pluck field '{pluck_path}'"
                                )
                            # SA-1814 (G4): index-aligned — emit null at this index
                            # instead of dropping the element, so the output list
                            # length always equals the source array length (no skew
                            # for downstream zip-by-index).
                            flat_values.append(None)
                            continue
                        flat_values.append(val)

                    self._set_nested(mapped_data, array_cfg.target_field, flat_values)
                    applied_rules_count += 1
                    continue

                # -- Default "objects" mode: map each item to a dict --
                mapped_items: list[dict[str, Any]] = []
                for idx, item in enumerate(source_array):
                    if not isinstance(item, dict):
                        item = {"_value": item}

                    mapped_item: dict[str, Any] = {}
                    for sub_rule in array_cfg.item_mappings:
                        try:
                            sub_value = self._resolve_rule_value(sub_rule, item)

                            if isinstance(sub_value, _Missing):
                                if strict_mode:
                                    errors.append(
                                        f"Array '{array_cfg.source_array_path}[{idx}]': "
                                        f"missing field '{sub_rule.source_path}'"
                                    )
                                continue

                            self._set_nested(mapped_item, sub_rule.target_field, self._coerce_value(
                                sub_value, sub_rule.field_type
                            ))
                        except Exception as exc:
                            errors.append(
                                f"Array '{array_cfg.source_array_path}[{idx}]' "
                                f"rule '{sub_rule.source_path}': {exc}"
                            )

                    mapped_items.append(mapped_item)

                self._set_nested(mapped_data, array_cfg.target_field, mapped_items)
                applied_rules_count += 1

            except Exception as exc:
                errors.append(f"Array mapping '{array_cfg.source_array_path}': {exc}")

        return {
            "mapped_data": mapped_data,
            "applied_rules_count": applied_rules_count,
            "skipped_rules": skipped_rules,
            "errors": errors,
        }

    def _resolve_rule_value(self, rule: MappingRule, source_data: dict[str, Any]) -> Any:
        """Resolve the value for a single mapping rule."""
        if rule.mapping_mode == MappingMode.CONSTANT.value:
            if rule.default_value is not None:
                return rule.default_value
            return MISSING

        # DIRECT mode
        value: Any = self._get_nested_value(source_data, rule.source_path)

        if isinstance(value, _Missing) or value is None:
            if rule.default_value is not None:
                return rule.default_value
            # SA-1813: a MISSING source OR a null value (no default) is treated as
            # missing -> subject to on_missing (skip/null/error). Previously a null
            # passed through and coerced to the literal string "None" (a footgun).
            return MISSING

        return value

    def _on_missing_action(self, policy: str, rule: MappingRule,
                           skipped_rules: list[str], errors: list[str]) -> tuple[str, Any]:
        """Resolve a MISSING source per ``on_missing``. Returns (action, value)
        where action is ``"write"`` (write ``value``) or ``"skip"``."""
        if policy == "null":
            return "write", None
        if policy == "error":
            errors.append(
                f"Missing source field: '{rule.source_path}' (target: '{rule.target_field}')"
            )
            return "skip", None
        # skip (default)
        skipped_rules.append(rule.source_path)
        return "skip", None

    def _on_type_error_action(self, policy: str, rule: MappingRule, raw: Any,
                              exc: Exception, errors: list[str]) -> tuple[str, Any]:
        """Resolve a coercion failure per ``on_type_error``."""
        if policy == "keep":
            return "write", raw
        if policy == "null":
            return "write", None
        # error (default)
        errors.append(f"Rule '{rule.source_path}' -> '{rule.target_field}': {exc}")
        return "skip", None

    @staticmethod
    def _set_nested(dest: dict[str, Any], path: str, value: Any) -> None:
        """Write ``value`` at a dotted ``target`` path, building intermediate dicts.

        A flat key (no dot) is a plain assignment (unchanged behavior). For a
        dotted path (``customer.name``) intermediate dicts are created; a
        non-dict intermediate is overwritten with a dict so the write always
        succeeds. Target paths are dotted dict keys only (no ``[i]`` array
        writes) — SA-1813 scope.
        """
        if "." not in path:
            dest[path] = value
            return
        parts = path.split(".")
        cur: Any = dest
        for p in parts[:-1]:
            nxt = cur.get(p) if isinstance(cur, dict) else None
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def _get_nested_value(self, data: Any, path: str) -> Any:
        """Traverse a nested dict/list using dot notation with bracket indices.

        Examples:
            "name"                          → data["name"]
            "data.user.name"                → data["data"]["user"]["name"]
            "items[0].name"                 → data["items"][0]["name"]
            "data.items[1].tags[0]"         → data["data"]["items"][1]["tags"][0]
        """
        if not path:
            return MISSING

        segments: list[str] = self._parse_path(path)
        current: Any = data

        for segment in segments:
            if isinstance(current, _Missing):
                return MISSING

            # Check for array index
            if segment.startswith("[") and segment.endswith("]"):
                index_str: str = segment[1:-1]
                if not isinstance(current, list):
                    return MISSING
                try:
                    idx: int = int(index_str)
                except ValueError:
                    return MISSING
                if idx < 0 or idx >= len(current):
                    return MISSING
                current = current[idx]
            else:
                if not isinstance(current, dict):
                    return MISSING
                if segment not in current:
                    return MISSING
                current = current[segment]

        return current

    def _parse_path(self, path: str) -> list[str]:
        """Parse a dot-notation path into segments, splitting bracket indices.

        "data.items[0].name" → ["data", "items", "[0]", "name"]
        """
        segments: list[str] = []
        for part in path.split("."):
            # Split bracket notation: "items[0]" → "items", "[0]"
            tokens: list[str] = re.split(r"(\[[^\]]*\])", part)
            for token in tokens:
                token = token.strip()
                if token:
                    segments.append(token)
        return segments

    def _coerce_value(self, value: Any, field_type: str) -> Any:
        """Coerce a value to the target field type."""
        if field_type == MappingFieldType.STRING.value:
            return self._to_string(value)
        if field_type == MappingFieldType.NUMBER.value:
            return self._to_number(value)
        if field_type == MappingFieldType.BOOLEAN.value:
            return self._to_boolean(value)
        if field_type == MappingFieldType.OBJECT.value:
            return self._to_object(value)
        if field_type == MappingFieldType.ARRAY.value:
            return self._to_array(value)
        return value

    def _to_string(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    def _to_number(self, value: Any) -> int | float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                pass
            try:
                return float(value)
            except ValueError:
                raise TypeCoercionError(f"Cannot coerce '{value}' to number")
        raise TypeCoercionError(f"Cannot coerce {type(value).__name__} to number")

    def _to_boolean(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lower: str = value.lower().strip()
            if lower in ("true", "1", "yes"):
                return True
            if lower in ("false", "0", "no"):
                return False
            raise TypeCoercionError(f"Cannot coerce '{value}' to boolean")
        raise TypeCoercionError(f"Cannot coerce {type(value).__name__} to boolean")

    def _to_object(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed: Any = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
                raise TypeCoercionError(f"JSON string parsed to {type(parsed).__name__}, expected object")
            except json.JSONDecodeError as exc:
                raise TypeCoercionError(f"Cannot parse '{value}' as JSON object: {exc}") from exc
        raise TypeCoercionError(f"Cannot coerce {type(value).__name__} to object")

    def _to_array(self, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
                raise TypeCoercionError(f"JSON string parsed to {type(parsed).__name__}, expected array")
            except json.JSONDecodeError as exc:
                raise TypeCoercionError(f"Cannot parse '{value}' as JSON array: {exc}") from exc
        raise TypeCoercionError(f"Cannot coerce {type(value).__name__} to array")
