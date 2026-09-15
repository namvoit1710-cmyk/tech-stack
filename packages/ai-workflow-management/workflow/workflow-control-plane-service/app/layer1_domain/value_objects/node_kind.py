"""Node functional-kind vocabulary + pure smart default (SA-1734 / SA-1736).

Pure (no repo, no I/O) so it can run inside NodeDefinition.__post_init__ to
heal existing rows on read — mirroring the base_worker_id self-heal. Repo-aware
parent inheritance lives in the use-case layer (F/SA-1740).
"""
import logging
from enum import Enum
from typing import Any

from app.layer1_domain.value_objects.node_type import NodeType

_log = logging.getLogger(__name__)


class NodeKind(str, Enum):
    """The closed (kind) vocabulary. ``str, Enum`` is wire-compatible — JSON /
    HANA / proto serialisation is unchanged — but membership is now checked by
    construction (``NodeKind(value)``) instead of a bare tuple scan, so a
    drifted value (e.g. the executor's invented ``"write"``) raises
    ``ValueError`` instead of silently persisting."""

    TRIGGER = "trigger"
    ACTION = "action"
    READ = "read"
    LOGIC = "logic"
    HUMAN = "human"
    TRANSFORM = "transform"
    UTIL = "util"


# Derived from the enum — nothing can drift from NodeKind any more.
VALID_NODE_KINDS = tuple(k.value for k in NodeKind)

# A business collection bucket name is user-named free text EXCEPT these two
# reserved buckets: every builtin/engine node lives under Core, and the
# default bucket for a tenant-authored definition with no collection set is
# My Nodes. Centralised here (next to NodeKind) so no call site hardcodes
# either string.
CORE_COLLECTION = "Core"
MINE_COLLECTION = "My Nodes"


# SA-1734/FIX2: the single source of truth for "what kind is this BUILTIN node
# type?" Keyed by the NodeType ENUM — not by a category string — so a new
# NodeType that reaches the catalog without an entry here is a visible gap
# (the totality test in test_kind_vocab_parity.py / test_builtin_node_provider_category.py
# fails loudly) instead of a silent ``dict.get(cat, "action")`` fallback.
#
# This preserves the previous (now-deleted) builtin_node_provider._CATEGORY_KIND
# behaviour exactly, with ONE correction: WORKFLOW (category
# "control_flow.composition") is "logic", not "action" — that category key was
# simply missing from the old map, so a sub-workflow node silently fell back to
# "action" and rendered under "Data & Files" instead of "Flow & Logic".
_KIND_BY_NODE_TYPE: dict[NodeType, NodeKind] = {
    NodeType.TRIGGER: NodeKind.TRIGGER,
    NodeType.CONDITION: NodeKind.LOGIC,
    NodeType.SWITCH: NodeKind.LOGIC,
    NodeType.LOOP: NodeKind.LOGIC,
    NodeType.PARALLEL: NodeKind.LOGIC,
    NodeType.MERGE: NodeKind.LOGIC,
    NodeType.WAIT: NodeKind.LOGIC,
    NodeType.WORKFLOW: NodeKind.LOGIC,  # corrected: was an unlisted key -> "action" fallback
    NodeType.COMPUTE: NodeKind.TRANSFORM,
    NodeType.SET_VARIABLE: NodeKind.TRANSFORM,
    NodeType.OUTPUT: NodeKind.UTIL,
    NodeType.APPROVAL_FLOW: NodeKind.HUMAN,
    NodeType.DATA_EDIT: NodeKind.HUMAN,
    NodeType.TEMPLATE_DATA_EDIT: NodeKind.HUMAN,
    NodeType.FILE_UPLOAD: NodeKind.HUMAN,
    NodeType.HUMAN_ACTION: NodeKind.HUMAN,
    NodeType.INPUT: NodeKind.HUMAN,
    NodeType.PROMPT: NodeKind.ACTION,
    # TASK (and the internal-only LOOP_EXIT/LOOP_CONTINUE) are deliberately
    # absent: TASK is worker-backed, so its kind depends on what the worker
    # does (inferred from the locked HTTP verb in ``default_kind`` below).
}

_READ_METHODS = frozenset({"GET", "HEAD"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _coerce_node_type(node_type: Any) -> NodeType | None:
    if isinstance(node_type, NodeType):
        return node_type
    try:
        return NodeType(str(node_type))
    except ValueError:
        return None


def kind_for_node_type(node_type: Any) -> NodeKind | None:
    """The static kind of a builtin node type.

    ``None`` for TASK (worker-backed — its kind depends on what the worker
    does, so it is inferred from the locked HTTP verb) and for anything not
    in the static map.
    """
    nt = _coerce_node_type(node_type)
    if nt is None:
        return None
    return _KIND_BY_NODE_TYPE.get(nt)


def default_kind(base_type: Any, locked_defaults: dict | None) -> NodeKind:
    """Deterministic default kind for a node definition.

    Rule: a builtin/control-flow ``base_type`` inherits ITS static kind
    (``kind_for_node_type``); otherwise (TASK / worker-backed) infer from the
    locked HTTP verb (GET/HEAD -> read; POST/PUT/PATCH/DELETE -> action);
    else action.
    """
    static = kind_for_node_type(base_type)
    if static is not None:
        return static
    method = str((locked_defaults or {}).get("method", "")).upper()
    if method in _READ_METHODS:
        return NodeKind.READ
    if method in _WRITE_METHODS:
        return NodeKind.ACTION
    return NodeKind.ACTION


def _as_node_kind_or_raw(value: str) -> Any:
    """Coerce ``value`` to a ``NodeKind`` when it's a real vocabulary member;
    otherwise return it unchanged. Keeps ``resolve_kind`` permissive on bad
    input (same posture as before this fix) so an invalid explicit/inherited
    kind still reaches ``NodeDefinition.validate()``'s existing
    ``kind not in VALID_NODE_KINDS`` check and comes back as a clean
    ``VALIDATION_ERROR`` — instead of an unhandled ``ValueError`` crashing the
    use case before validation ever runs.
    """
    try:
        return NodeKind(value)
    except ValueError:
        return value


def resolve_kind(
    explicit_kind: str,
    parent: Any,
    base_type: Any,
    locked_defaults: dict | None,
) -> Any:
    """Parent-aware smart default for a node definition's kind (F/SA-1740).

    Priority: explicit user value -> inherit the (already-resolved) parent's kind
    -> pure ``default_kind``. ``parent`` is the parent NodeDefinition the caller
    already fetched (or None); passing it in keeps this pure + unit-testable and
    reuses the create use-case's existing parent read. This is the repo-aware
    inheritance step the module docstring reserves for the use-case layer.
    """
    if explicit_kind:
        return _as_node_kind_or_raw(explicit_kind)
    parent_kind = getattr(parent, "kind", "") if parent is not None else ""
    if parent_kind:
        return _as_node_kind_or_raw(parent_kind)
    return default_kind(base_type, locked_defaults)


def safe_kind_value(raw: Any, *, context: str = "") -> str:
    """Coerce anything — an enum, a wire string, a legacy DB value, junk, None —
    into a VALID kind *string*. **Never raises.**

    This is the last stop before the catalog DTO, and the DTO's ``kind: NodeKind``
    is a pydantic field: handing it an unknown value raises a ValidationError, and
    that ValidationError happens while ASSEMBLING the catalog. The blast radius is
    not one node — it is ``/api/v2/node-catalog`` returning 500 and every tenant
    getting an empty palette, because one worker registered ``kind="banana"`` or one
    legacy row holds a value we retired.

    So: unknown -> ACTION, and say so loudly in the logs. A node in the wrong bucket
    is a cosmetic bug. A dead palette is an outage. Never pay for the first with the
    second. (Boundaries that CAN reject — the executor's HTTP/gRPC registration doors
    — still reject; this is the read path, and a reader must be forgiving.)

    Returning ``str`` (not ``NodeKind``) is deliberate: callers build the category as
    an f-string, and ``f"{NodeKind.ACTION}"`` renders ``"NodeKind.ACTION"``, not
    ``"action"``. That exact bug shipped once already.
    """
    if isinstance(raw, NodeKind):
        return raw.value
    if not raw:
        return NodeKind.ACTION.value
    try:
        return NodeKind(raw).value
    except ValueError:
        _log.warning(
            "unknown node kind %r%s — coercing to %r so the catalog still renders. "
            "Valid kinds: %s",
            raw, f" ({context})" if context else "", NodeKind.ACTION.value,
            ", ".join(VALID_NODE_KINDS),
        )
        return NodeKind.ACTION.value
