"""Delivery-mode vocabulary for how the executor hands a task to a worker (SA-2025/SA-2026).

The executor is the network boundary: it receives a worker's registration off the
wire (HTTP and gRPC) and it is the service that decides — per registered worker —
whether a task is *pushed* (executor POSTs `/execute-async`, the legacy default) or
*pulled* (executor stores the task; the worker leases it). Mode is a property of the
worker's registration, transparent to the control plane: WCP dispatches identically
either way. Like ``node_kind``, this is a parallel per-layer definition of a wire
vocabulary — the executor validates it itself rather than trust the sender, and it is
a separate deployable from the control plane so it cannot import the CP's copy.

``DeliveryMode`` subclasses ``str`` so it is wire-compatible: JSON and proto
serialisation are unchanged, and existing ``== "push"``-style comparisons keep
working untouched.
"""
from enum import Enum
from typing import Optional, Union


class DeliveryMode(str, Enum):
    PUSH = "push"
    PULL = "pull"


def normalize_delivery_mode(raw: Optional[Union[str, "DeliveryMode"]]) -> DeliveryMode:
    """Normalize a raw wire value (HTTP JSON string or proto3 field) to DeliveryMode.

    An empty/blank value means "not supplied". proto3 has no concept of an absent
    scalar field — an old worker that predates SA-2026 sends "" for `delivery_mode`
    — so blank must default to DeliveryMode.PUSH (the pre-existing behaviour), never
    be rejected. This is the load-bearing back-compat guarantee: every worker that
    ever registered before this field existed keeps the identical push path.

    A non-empty value that is not in the vocabulary is a real error (e.g. "poll",
    "queue") and must raise ValueError so the caller can turn it into a 422 (HTTP)
    or INVALID_ARGUMENT (gRPC).
    """
    value = (raw or "").strip()
    if not value:
        return DeliveryMode.PUSH
    return DeliveryMode(value)
