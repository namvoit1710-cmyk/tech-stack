"""``RoutingHandlerBase`` — template method for branching handlers.

Branching handlers (CONDITION, SWITCH) share the same fan-out
mechanics: pick a port → walk outgoing edges → record which ones the
choice accepts in ``taken_edge_ports``. Only the *selection* logic
differs (CONDITION evaluates a boolean expression and picks then/else;
SWITCH evaluates structured rules and matches an explicit port_id or
branch label).

This base owns the mechanics; subclasses implement ``select_port`` and
return a :class:`RoutingDecision` describing the selection.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import ClassVar

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.base_node_handler import (
    BaseNodeHandler,
    RoutingDecision,
)
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory

logger = logging.getLogger(__name__)


class RoutingHandlerBase(AsyncInlineHandlerBase):
    """Template method for branching handlers.

    Subclasses implement :meth:`select_port` and return a
    :class:`RoutingDecision`. The base ``handle`` then:

    1. Sets ``completion_mode = "immediate"`` and stamps
       ``state_updates[node.id] = {"output": {}, "metadata":
       {"type": <METADATA_TYPE>}}``.
    2. Calls the subclass's :meth:`select_port`.
    3. Folds ``decision.completion_payload`` into
       ``result.completion_payload`` (lands in the ``NodeCompleted``
       event).
    4. Iterates outgoing edges and lets ``decision.edge_accepts``
       decide which ones to record in ``taken_edge_ports`` and with
       what port label.

    The downstream ``DispatcherConsumer`` reads
    ``NodeOutputProduced.taken_edge_ports`` and emits ``EdgeTraversed``
    events from there — branching handlers do **not** emit
    ``EdgeTraversed`` directly.
    """

    METADATA_TYPE: ClassVar[str] = "routing"

    @abstractmethod
    async def select_port(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> RoutingDecision:
        """Return the selection for this node.

        See :class:`RoutingDecision`. ``edge_accepts`` is consulted once
        per outgoing edge; returning ``None`` from it skips the edge,
        returning a string records that string as the port label in
        ``taken_edge_ports``.
        """
        ...

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()
        result.completion_mode = "immediate"
        result.state_updates[node.id] = {
            "output": {},
            "metadata": {"type": self.METADATA_TYPE},
        }

        decision = await self.select_port(node, run)
        result.completion_payload = dict(decision.completion_payload)
        if decision.output:
            result.state_updates[node.id]["output"] = dict(decision.output)

        outgoing = topology.edge_map.get(node.id, [])
        for edge in outgoing:
            label = decision.edge_accepts(edge)
            if label is not None:
                result.taken_edge_ports[edge.id] = label
        # SA-1754: a branching node whose chosen branch has no wired edge
        # selects nothing → the engine's Mode-2 guard dispatches no successor
        # and the run silently dead-ends. SWITCH already warns on this; emit
        # the symmetric warning here so CONDITION dead-ends are observable
        # too (e.g. a False result whose only edge is an unlabeled/"then"
        # edge, or any chosen branch left unwired on the canvas).
        if outgoing and not result.taken_edge_ports:
            logger.warning(
                "%s node %s: no outgoing edge accepted the selected branch "
                "(branch=%r) — node produces no successor, which may stall the "
                "run. Check that the chosen branch has a wired edge.",
                self.METADATA_TYPE,
                node.id,
                result.completion_payload.get("branch"),
            )
        result.state_updates[node.id]["metadata"]["taken_edge_ports"] = result.taken_edge_ports
        return result


__all__ = ["RoutingHandlerBase"]
