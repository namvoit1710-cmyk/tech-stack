"""Spec-driven split of FE ``parameters`` into engine config/bindings.

Replaces the hand-maintained ``_CONFIG_KEYS`` / ``_CONFIG_TYPE_MAP``
tables in ``entities/workflow.py`` (deleted in Phase 5): which keys are
config, and how values coerce, both derive from the node's spec.

The one declared value *transform* is ``ConfigField.store_as="dict"``:
the FE's array-of-rows widget value (``[{param_name, param_value}]``)
folds into the ``{name: expression}`` dict the engine consumes — e.g.
WORKFLOW ``input_mapping``. Everything else is pure routing + coercion.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.node_spec.model import ConfigField, NodeSpec, coerce_value


def split_parameters(
    spec: NodeSpec, parameters: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split FE ``parameters`` into engine ``(config, input_binding)``."""
    if not parameters:
        return {}, {}
    by_key = {f.key: f for f in spec.config_fields}
    config: dict[str, Any] = {}
    binding: dict[str, Any] = {}
    for key, value in parameters.items():
        f = by_key.get(key)
        if f is None:
            binding[key] = value
            continue
        config[key] = coerce_value(f, _apply_store_as(f, value))
    return config, binding


def merge_parameters(
    config: dict[str, Any], input_binding: dict[str, Any],
) -> dict[str, Any]:
    """Merge engine ``(config, input_binding)`` back into FE ``parameters``.

    Same contract as the legacy function: ``_``-prefixed internal keys
    are excluded (the smuggled definition snapshots, until Phase 4 moves
    them off config entirely).
    """
    merged = {k: v for k, v in config.items() if not k.startswith("_")}
    merged.update(input_binding)
    return merged


def _apply_store_as(f: ConfigField, value: Any) -> Any:
    if f.store_as != "dict" or not isinstance(value, list):
        return value
    # Fold [{<name_key>: ..., <value_key>: ...}] rows into a dict, using
    # the first two item_fields as the (name, value) columns. Dicts pass
    # through untouched (API-authored nodes already send the engine shape).
    if len(f.item_fields) < 2:
        return value
    name_key = f.item_fields[0].key
    value_key = f.item_fields[1].key
    folded: dict[str, Any] = {}
    for row in value:
        if isinstance(row, dict) and name_key in row and value_key in row:
            folded[str(row[name_key])] = row[value_key]
    return folded
