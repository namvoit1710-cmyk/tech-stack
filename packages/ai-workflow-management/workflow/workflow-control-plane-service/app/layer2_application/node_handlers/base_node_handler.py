"""``BaseNodeHandler`` — the ABC root of the node-handler hierarchy.

The previous ``NodeHandler`` Protocol let every handler stand on its
own with no enforced inheritance. This file replaces it with an
explicit ABC tree so the four execution shapes (PURE_INLINE,
ASYNC_INLINE, DISPATCH_EXTERNAL, PENDING_USER) become typed families
and PENDING handlers can declare their schema provider in one place.

The ABC contract is intentionally minimal: subclasses implement
``handle()`` and (for PENDING handlers) override ``get_schema_provider``.
Everything else — template-method orchestration for routing and
pending, marker classes for the simpler shapes — lives in sibling
files (``routing_base.py``, ``pending_base.py``, ``inline_bases.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Optional
from collections.abc import Callable

from app.layer2_application.node_handlers.base import (
    NodeHandlerResult,
    NodeHandlerShape,
)
from app.layer2_application.node_handlers.schema_providers import SchemaProvider

if TYPE_CHECKING:
    from app.layer1_domain.entities.task import Task
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
    from app.layer2_application.orchestration.task_factory import TaskFactory


class BaseNodeHandler(ABC):
    """Common ancestor of every node handler.

    Subclasses set ``shape`` as a ClassVar so the timing wrapper
    (``invoke_handler``) can label metrics without an instance attribute.
    ``get_schema_provider`` is optional — handlers that don't drive a
    ``/schema`` endpoint return ``None`` and ``GetNodeSchemaUseCase``
    short-circuits with ``schema_source = "none"``.
    """

    shape: ClassVar[NodeHandlerShape] = NodeHandlerShape.UNKNOWN

    @abstractmethod
    async def handle(
        self,
        node: "WorkflowNode",
        run: "WorkflowRun",
        workflow: "Workflow",
        topology: "GraphTopology",
        completed_task: "Task | None",
        task_factory: "TaskFactory",
    ) -> NodeHandlerResult:
        ...

    def get_schema_provider(self) -> Optional[SchemaProvider]:
        """Return the schema provider for ``GET /schema``, or ``None``.

        Default ``None`` means "this node has no /schema surface".
        ``PendingHandlerBase`` overrides this to return a
        ``StaticSchemaProvider``; ``DataEditNodeHandler`` further
        overrides at construction time to swap in ``DataEditSchemaProvider``.
        """
        return None


@dataclass
class RoutingDecision:
    """The result of a routing handler's ``select_port`` call.

    The base ``RoutingHandlerBase.handle()`` consumes this to:

    * populate ``NodeHandlerResult.taken_edge_ports`` for every outgoing
      edge that ``edge_accepts`` accepts (the closure returns the port
      label to record, or ``None`` to skip the edge);
    * fold ``completion_payload`` into the ``NodeCompleted`` event so
      downstream observers see ``branch`` / ``expression`` /
      ``condition_result`` (the canonical CONDITION+SWITCH shape).

    ``matched_branch`` and ``matched_port_id`` are informational — they
    let subclasses describe what they selected without re-deriving it
    from the edges. ``edge_accepts`` is the single source of truth for
    which edges propagate.
    """

    matched_branch: Optional[str]
    matched_port_id: str
    completion_payload: dict[str, Any] = field(default_factory=dict)
    edge_accepts: Callable[[Any], Optional[str]] = lambda _edge: None
    # Node-output payload — ``RoutingHandlerBase.handle`` writes this into
    # ``state_updates[node.id]["output"]`` so the evaluation trace
    # (result / branch / expression / resolved) is visible in the node's
    # output panel, not only in the NodeCompleted event.
    output: dict[str, Any] = field(default_factory=dict)


__all__ = ["BaseNodeHandler", "RoutingDecision"]
