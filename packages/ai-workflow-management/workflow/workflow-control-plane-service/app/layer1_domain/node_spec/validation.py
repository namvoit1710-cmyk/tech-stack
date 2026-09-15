"""Save-time validation of node config against its spec.

Pure domain logic — no I/O, no resolver. The layer-2 orchestrator
(``services/node_config_validator.py``) walks a workflow's nodes and
collects every violation in one pass so the FE can mark all offending
fields at once.

Violation codes (design §4.3):

* ``unknown_key``       — config key the spec doesn't declare
* ``missing_required``  — required and *visible* but absent/empty
* ``invalid_enum``      — value not in the field's choices
* ``invalid_type``      — wrong type after coercion
* ``invalid_expression``— malformed ``{{…}}`` template syntax
* ``exclusive_group``   — ``requires_one_of`` violated
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.expression_syntax import validate_expression_syntax
from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    coerce_value,
)


@dataclass(frozen=True)
class SpecViolation:
    node_id: str
    key: str
    code: str
    message: str
    severity: str = "error"  # "error" | "warn"

    def to_dict(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "key": self.key,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


def _is_template(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def validate_node(spec: NodeSpec, node: Any) -> list[SpecViolation]:
    """Validate ``node.config`` against the spec. Pure; ``node`` is
    duck-typed (needs ``.id`` and ``.config``)."""
    node_id = getattr(node, "id", "") or ""
    config: dict[str, Any] = getattr(node, "config", None) or {}
    violations: list[SpecViolation] = []

    # 1. unknown_key — drift caught at the door. `_`-prefixed keys are
    # the smuggled definition snapshots; exempt until Phase 4 moves them
    # to WorkflowNode.definition_snapshot.
    for key in config:
        if key.startswith("_"):
            continue
        if key not in spec.config_keys:
            violations.append(SpecViolation(
                node_id, key, "unknown_key",
                f"{key!r} is not a config key of {spec.node_type.value}",
            ))

    for f in spec.config_fields:
        value = config.get(f.key)
        visible = f.visible_when is None or f.visible_when.matches(config)

        # 2. missing_required — only when the field is visible, and only
        # when the spec has no default (parse_config fills defaults, so
        # an absent defaulted field is satisfied, not missing).
        if f.required and visible and _is_empty(value) and f.default is None:
            cond = (
                f" when {f.visible_when.field} is {f.visible_when.equals!r}"
                if f.visible_when else ""
            )
            violations.append(SpecViolation(
                node_id, f.key, "missing_required",
                f"{f.key} is required{cond}",
            ))
            continue
        if _is_empty(value):
            continue

        violations.extend(_validate_value(node_id, f, value))

    # 6. exclusive_group — at most one key per group may be set (error);
    # none set is a WARN: a just-dragged CONDITION/SWITCH node is a
    # legitimate draft state and must not block saves in enforce mode
    # (it evaluates to the empty-expression default at runtime).
    for group in spec.requires_one_of:
        set_keys = [k for k in group if not _is_empty(config.get(k))]
        if len(set_keys) > 1:
            violations.append(SpecViolation(
                node_id, group[0], "exclusive_group",
                f"{set_keys} are mutually exclusive — set exactly one "
                f"of {list(group)}",
            ))
        elif not set_keys:
            violations.append(SpecViolation(
                node_id, group[0], "exclusive_group",
                f"none of {list(group)} is configured — the node will "
                "take its default branch at runtime",
                severity="warn",
            ))

    return violations


def validate_bindings(spec: NodeSpec, node: Any) -> list[SpecViolation]:
    """WARN-level check of ``input_binding`` keys against declared inputs.

    Only meaningful when the spec declares ``input_fields`` (worker /
    business nodes with a registered schema). Warn — not error — because
    worker schemas evolve independently of saved workflows; hard-failing
    on extra binding keys would break version skew (design §7).
    """
    if not spec.input_fields:
        return []
    node_id = getattr(node, "id", "") or ""
    binding: dict[str, Any] = getattr(node, "input_binding", None) or {}
    declared = {f.key for f in spec.input_fields}
    return [
        SpecViolation(
            node_id, key, "unknown_binding",
            f"{key!r} is not a declared input of this {spec.runtime} node",
            severity="warn",
        )
        for key in binding
        if key not in declared and not key.startswith("_")
    ]


def _validate_value(
    node_id: str, f: ConfigField, value: Any,
) -> list[SpecViolation]:
    out: list[SpecViolation] = []

    # 5. invalid_expression — syntax-only, on expression-capable fields.
    if f.expression and isinstance(value, str):
        error = validate_expression_syntax(value)
        if error:
            out.append(SpecViolation(
                node_id, f.key, "invalid_expression", f"{f.key}: {error}",
            ))
            return out
        if _is_template(value):
            # A (well-formed) template defers enum/type checks to runtime.
            return out

    # 3. invalid_enum
    if f.choices:
        allowed = [c.value for c in f.choices]
        if value not in allowed:
            out.append(SpecViolation(
                node_id, f.key, "invalid_enum",
                f"{f.key} must be one of {'|'.join(allowed)}, got {value!r}",
            ))
        return out

    # 4. invalid_type — post-coercion.
    coerced = coerce_value(f, value)
    if f.type == FieldType.NUMBER:
        if not isinstance(coerced, (int, float)) or isinstance(coerced, bool):
            out.append(_type_violation(node_id, f, value, "a number"))
    elif f.type == FieldType.BOOLEAN:
        if not isinstance(coerced, bool):
            out.append(_type_violation(node_id, f, value, "a boolean"))
    elif f.type == FieldType.ARRAY:
        if not isinstance(coerced, (list, tuple)):
            out.append(_type_violation(node_id, f, value, "an array"))
        elif f.item_fields:
            out.extend(_validate_items(node_id, f, coerced))
    elif f.type == FieldType.OBJECT:
        if f.store_as == "dict" and isinstance(coerced, list):
            # Unfolded rows reaching validation means split didn't run
            # (API-authored config) — still a shape error.
            out.append(_type_violation(node_id, f, value, "an object"))
        elif not isinstance(coerced, (dict, list)):
            out.append(_type_violation(node_id, f, value, "an object"))
    elif f.type == FieldType.JSON:
        if isinstance(coerced, str):  # coercion failed to parse it
            out.append(_type_violation(node_id, f, value, "valid JSON"))
    return out


def _validate_items(
    node_id: str, f: ConfigField, items: Any,
) -> list[SpecViolation]:
    """Recurse into ARRAY rows: required + enum + type per item field.

    Lenient about extra keys inside rows (FE widgets add bookkeeping);
    strict about declared ones.
    """
    out: list[SpecViolation] = []
    for idx, row in enumerate(items):
        if not isinstance(row, dict):
            out.append(SpecViolation(
                node_id, f.key, "invalid_type",
                f"{f.key}[{idx}] must be an object, got {type(row).__name__}",
            ))
            continue
        for item_field in f.item_fields:
            item_value = row.get(item_field.key)
            visible = (
                item_field.visible_when is None
                or item_field.visible_when.matches(row)
            )
            if item_field.required and visible and _is_empty(item_value):
                out.append(SpecViolation(
                    node_id, f"{f.key}[{idx}].{item_field.key}",
                    "missing_required",
                    f"{f.key}[{idx}].{item_field.key} is required",
                ))
                continue
            if _is_empty(item_value):
                continue
            for v in _validate_value(node_id, item_field, item_value):
                out.append(SpecViolation(
                    node_id, f"{f.key}[{idx}].{item_field.key}",
                    v.code, v.message, v.severity,
                ))
    return out


def _type_violation(
    node_id: str, f: ConfigField, value: Any, expected: str,
) -> SpecViolation:
    return SpecViolation(
        node_id, f.key, "invalid_type",
        f"{f.key} must be {expected}, got {value!r}",
    )
