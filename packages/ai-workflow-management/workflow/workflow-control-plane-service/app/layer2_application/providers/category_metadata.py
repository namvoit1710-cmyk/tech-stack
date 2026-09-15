"""Display metadata for node-catalog category keys.

Category keys are hierarchical dotted paths (e.g. ``control_flow.branching``).
This module maps each known key — top-level and leaf — to a human label and a
sort order, so the frontend can render the palette tree without humanizing
the raw key itself.

Unknown keys fall back to a title-cased last segment and a high order value,
so the backend stays robust when new categories appear before labels are
registered here.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.layer1_domain.value_objects.node_kind import CORE_COLLECTION, MINE_COLLECTION


_CATEGORY_METADATA: dict[str, tuple[str, int]] = {
    # Top-level groups (shown as section headers in the palette).
    "control_flow":               ("Control Flow",        1),
    "human_in_the_loop":          ("Human in the Loop",   2),
    "worker":                     ("Workers",             3),
    "business":                   ("Business",            4),

    # control_flow.*
    "control_flow.entry":         ("Entry",               1),
    "control_flow.branching":     ("Branching",           2),
    "control_flow.iteration":     ("Iteration",           3),
    "control_flow.concurrency":   ("Concurrency",         4),
    "control_flow.composition":   ("Composition",         5),
    "control_flow.transform":     ("Transform",           6),
    "control_flow.grouping":      ("Grouping",            7),

    # human_in_the_loop.*
    "human_in_the_loop.input":    ("Input",               1),
    "human_in_the_loop.action":   ("Action",              2),
    "human_in_the_loop.data":     ("Data Edit",           3),
    "human_in_the_loop.file":     ("File Upload",         4),

    # worker.*
    "worker.integration":         ("Integration",         1),
}


_UNKNOWN_ORDER = 9999


# SA-1737: kind sub-group display. Keys MUST equal VALID_NODE_KINDS (parity test).
_KIND_LABEL: dict[str, str] = {
    "trigger": "Triggers", "action": "Actions", "read": "Find & Read",
    "logic": "Logic & Flow", "human": "Human Tasks", "transform": "Transform",
    "util": "Utilities",
}
_KIND_ORDER: dict[str, int] = {
    "trigger": 1, "action": 2, "read": 3, "logic": 4, "human": 5, "transform": 6, "util": 7,
}


def get_category_info(key: str) -> tuple[str, int]:
    """Return ``(label, order)`` for a category key.

    SA-1734 keys are ``<collection>.<kind>`` (leaf) or a bare ``<collection>``
    (top level). Kind sub-groups get a fixed label/order; collections order as
    Core(0) < connectors/custom(10) < the default "My Nodes" bucket(90). Any
    unrecognized key falls back to the legacy registry then a humanized segment.
    """
    if "." in key:
        kind = key.rsplit(".", 1)[-1]
        if kind in _KIND_LABEL:
            return (_KIND_LABEL[kind], _KIND_ORDER[kind])
    else:
        if key == CORE_COLLECTION:
            return (CORE_COLLECTION, 0)
        if key == MINE_COLLECTION:
            return (MINE_COLLECTION, 90)
        return (key, 10)  # a connector ("Jira") or a custom business collection
    if key in _CATEGORY_METADATA:
        return _CATEGORY_METADATA[key]
    last = key.rsplit(".", 1)[-1]
    label = last.replace("_", " ").title() if last else key
    return (label or key, _UNKNOWN_ORDER)


def expand_with_parents(keys: Iterable[str]) -> set[str]:
    """Expand dotted keys to include every ancestor path.

    ``{"a.b.c"}`` → ``{"a", "a.b", "a.b.c"}``.
    """
    out: set[str] = set()
    for k in keys:
        if not k:
            continue
        parts = k.split(".")
        for i in range(1, len(parts) + 1):
            out.add(".".join(parts[:i]))
    return out
