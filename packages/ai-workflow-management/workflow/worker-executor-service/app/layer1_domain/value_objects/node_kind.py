"""Functional-kind vocabulary for the executor's own domain (SA-1734/SA-1735).

The executor is the network boundary: it is the service that receives a
worker's registration off the wire (HTTP and gRPC), before the control plane
or the frontend ever see it. It must validate `kind` itself rather than trust
the sender — this is a separate deployable from the control plane, so it
cannot import the control plane's copy of this vocabulary
(`workflow-control-plane-service/app/layer1_domain/value_objects/node_kind.py`).
The proto contract is what's shared across services, not a Python module —
hence a parallel per-layer definition of the same wire vocabulary (mirrors the
worker-sdk's own `VALID_NODE_KINDS` tuple, guarded by its own parity test).

``NodeKind`` subclasses ``str`` so it is wire-compatible: JSON and proto
serialisation are unchanged, and existing ``== "action"``-style comparisons
keep working untouched.
"""
from enum import Enum
from typing import Optional, Union


class NodeKind(str, Enum):
    TRIGGER = "trigger"
    ACTION = "action"
    READ = "read"
    LOGIC = "logic"
    HUMAN = "human"
    TRANSFORM = "transform"
    UTIL = "util"


def normalize_kind(raw: Optional[Union[str, "NodeKind"]]) -> NodeKind:
    """Normalize a raw wire value (HTTP JSON string or proto3 field) to NodeKind.

    An empty/blank value means "not supplied". proto3 has no concept of an
    absent scalar field — an old worker that predates SA-1735 sends "" for
    `kind` — so blank must default to NodeKind.ACTION, not be rejected.

    A non-empty value that is not in the vocabulary is a real error (an
    invented kind, e.g. "banana" or "write") and must raise ValueError so the
    caller can turn it into a 422 (HTTP) or INVALID_ARGUMENT (gRPC).
    """
    value = (raw or "").strip()
    if not value:
        return NodeKind.ACTION
    return NodeKind(value)
