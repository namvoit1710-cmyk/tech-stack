"""Loop scope value object — Phase 4c.4 foundation.

Implements the ``LoopScope`` dataclass and ``derive_invocation_id``
helper from the Phase 3 LOOP event-driven spike
(``docs/design/loop-event-driven-spike.md`` §3.2).

A ``LoopScope`` represents **one level** of a (potentially nested)
loop's iteration context. A task executing inside a nested loop body
carries a ``scope_stack: list[LoopScope]`` with the outermost loop
first and the innermost loop at the tail — exactly the shape future
event-driven loop events will use for their ``scope_stack`` field.

Critical design property: the ``invocation_id`` is **stable across
iterations of the same loop invocation** but **different for each
outer iteration** of a nested loop. This is what lets a merge node
inside a nested inner body get a fresh quorum buffer per outer
iteration — without it, the outer iteration's merge state would leak
into the next outer iteration.

``derive_invocation_id`` uses ``uuid5`` over a loop-private namespace
so that:
- Same ``(run_id, loop_node_id, enclosing scopes)`` always produces
  the same id — deterministic replay.
- A nested loop gets a different id for each outer iteration because
  the enclosing scope_stack differs (outer iter changes).
- Invocation ids are globally unique within a run because they
  incorporate the run id into the derivation key.

**Phase 4c.4 scope: foundation only.** Nothing in the runtime
consumes these types yet. The loop handler continues to use the
existing ``Task.loop_context`` + ``Task.iteration_index`` scalar
fields. Future phases behind ``EVENT_DRIVEN_LOOP`` will migrate the
handler + engine to consume ``scope_stack`` and emit the new loop
event types.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from collections.abc import Sequence

# Private uuid5 namespace for loop invocation ids. Must remain stable
# for the lifetime of any persisted loop event log — changing it
# would invalidate every deterministic invocation id recorded under
# the old namespace. Chosen once, never modified.
_LOOP_INVOCATION_NAMESPACE = uuid.UUID("6f3c2c5e-8d5b-5f7e-9c4a-1b2d3e4f5a6b")


@dataclass(frozen=True)
class LoopScope:
    """One level of a loop's iteration context.

    Frozen so consumers cannot mutate a scope in-place. A task with
    multiple nested loop scopes carries an immutable
    ``tuple[LoopScope, ...]`` (or a plain list of frozen instances).

    Fields:
        loop_node_id: id of the LOOP workflow node whose iteration
            this scope represents.
        invocation_id: stable id for this loop invocation, derived
            via ``derive_invocation_id``. Same across all iterations
            of the same invocation; different for each outer
            iteration of a nested loop.
        iter: zero-based iteration index within this loop invocation.
            Changes between iterations even though ``invocation_id``
            stays the same.
        total: the total iteration count known at the time the loop
            started. For ``loop_type="count"`` / ``"collection"``
            this is fixed. For ``loop_type="condition"`` loops the
            total is unknown at start — the spike convention is to
            use ``-1`` as a sentinel meaning "unbounded / condition".
    """
    loop_node_id: str
    invocation_id: str
    iter: int
    total: int

    def to_dict(self) -> dict[str, object]:
        """Serialise for event-log payloads.

        Loop events in Phase 5 will carry ``scope_stack`` as a list of
        plain dicts so they round-trip through JSON in the event log's
        ``payload`` column. This method is the canonical serialisation
        format.
        """
        return {
            "loop_node_id": self.loop_node_id,
            "invocation_id": self.invocation_id,
            "iter": self.iter,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LoopScope":
        """Rebuild a LoopScope from its serialised form."""
        return cls(
            loop_node_id=str(data.get("loop_node_id", "")),
            invocation_id=str(data.get("invocation_id", "")),
            iter=int(data.get("iter", 0)),  # type: ignore[arg-type]
            total=int(data.get("total", 0)),  # type: ignore[arg-type]
        )


def derive_invocation_id(
    run_id: str,
    loop_node_id: str,
    enclosing: Sequence[LoopScope] | None = None,
) -> str:
    """Build a deterministic invocation id for a loop instantiation.

    The id is ``uuid5`` over a key composed of:

    - ``run_id`` — scopes to a single workflow run
    - ``loop_node_id`` — scopes to a specific LOOP node in the DAG
    - Each enclosing scope's ``(loop_node_id, invocation_id, iter)``
      triple — scopes to a specific outer iteration

    Args:
        run_id: id of the containing workflow run
        loop_node_id: id of the LOOP node being invoked
        enclosing: outer loop scopes (outermost first). Empty / None
            for a top-level loop.

    Returns:
        A 32-character hex string (uuid5 without dashes). Suitable for
        use directly in an event payload or as part of a deterministic
        event id correlation key.

    Examples:
        >>> # Top-level loop — no enclosing scopes
        >>> id1 = derive_invocation_id("run-1", "loop-outer")
        >>> id2 = derive_invocation_id("run-1", "loop-outer")
        >>> id1 == id2  # same inputs → same id
        True

        >>> # Nested loop — different outer iterations → different ids
        >>> outer_0 = LoopScope("loop-outer", id1, iter=0, total=2)
        >>> outer_1 = LoopScope("loop-outer", id1, iter=1, total=2)
        >>> inner_for_outer_0 = derive_invocation_id(
        ...     "run-1", "loop-inner", [outer_0],
        ... )
        >>> inner_for_outer_1 = derive_invocation_id(
        ...     "run-1", "loop-inner", [outer_1],
        ... )
        >>> inner_for_outer_0 != inner_for_outer_1  # critical property
        True
    """
    parts: list[str] = [run_id, loop_node_id]
    for scope in enclosing or ():
        parts.append(
            f"{scope.loop_node_id}#{scope.invocation_id}#{scope.iter}"
        )
    key = "|".join(parts)
    return uuid.uuid5(_LOOP_INVOCATION_NAMESPACE, key).hex
