"""Execute an ENGINE-builtin node definition standalone against ``input_data``.

Builds a synthetic single-node, in-memory context (no orchestration engine,
projector, file-backing, test-run, or ambient scope) and invokes the node's
registered handler — so the control plane can "test" a builtin business-node
definition the same way it tests a worker definition.

Only ENGINE node types that complete *inline* against the supplied input are
testable here (see :data:`StandaloneNodeRunner.INLINE_TESTABLE`). Types that
defer real work — LOOP (loop body), WORKFLOW (child run), PARALLEL — and PENDING
types (INPUT/HUMAN_ACTION/…) are filtered out by the caller; the
``completion_mode`` guard below is a second line of defence so this runner never
starts a child run or loop iterations.

Limitations (no ambient engine scope / no upstream graph):
  * ``{{$variables.X}}`` (the SET_VARIABLE runtime namespace) does not resolve —
    it is reachable only through the engine's ``SystemVariablesFrame``.
  * ``{{$<upstreamNode>.field}}`` references do not resolve.
A standalone test evaluates a node against the supplied ``input_data`` only.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.base import (
    NodeHandlerRegistry,
    NodeHandlerResult,
)
from app.layer2_application.orchestration.task_factory import TaskFactory


class NotTestableStandalone(Exception):
    """Raised when a node defers real work (child run / loop body / unmet merge
    quorum) and therefore cannot be meaningfully tested outside a workflow."""


class StandaloneNodeRunner:
    """Invokes an ENGINE-builtin handler against a synthetic in-memory context."""

    # ENGINE node types that complete inline against ``input_data`` alone.
    INLINE_TESTABLE = frozenset({
        NodeType.CONDITION,
        NodeType.SWITCH,
        NodeType.COMPUTE,
        NodeType.SET_VARIABLE,
        NodeType.MERGE,
    })

    def __init__(
        self,
        handler_registry: NodeHandlerRegistry,
        variable_resolver: Any = None,
    ) -> None:
        self._registry = handler_registry
        self._resolver = variable_resolver

    def is_inline_testable(self, node_type: NodeType) -> bool:
        return node_type in self.INLINE_TESTABLE

    async def run(
        self,
        node_def: NodeDefinition,
        input_data: dict[str, Any],
    ) -> NodeHandlerResult:
        synthetic_config = await self._build_config(node_def, input_data)
        # Seed both namespaces so {{$input.x}} and {{$custom.x}} resolve.
        seed_variables = {"input": dict(input_data), "custom": dict(input_data)}

        node = WorkflowNode(
            id=node_def.id,
            workflow_id=node_def.id,
            name=node_def.name or node_def.id,
            node_type=node_def.base_type,
            config=synthetic_config,
            definition_snapshot={
                "locked_defaults": node_def.locked_defaults,
                "input_mapping": node_def.input_mapping,
                "input_schema": node_def.input_schema,
            },
        )
        workflow = Workflow(name=f"__test__{node_def.id}", nodes=[node], edges=[])
        topology = GraphTopology.from_workflow(workflow)
        run = WorkflowRun(
            workflow_id=workflow.id,
            is_test=True,
            test_mode="node",
            context=RunContext(variables=seed_variables, topology=topology),
        )

        handler = self._registry.get(node_def.base_type)
        result = await handler.handle(
            node=node,
            run=run,
            workflow=workflow,
            topology=topology,
            completed_task=None,
            task_factory=TaskFactory(),
        )
        # Never start deferred work (child run / loop body / unmet quorum) here.
        if result.requires_child_run or result.completion_mode not in (
            "immediate",
            "absorbed",
        ):
            raise NotTestableStandalone(node_def.base_type.value)
        return result

    async def _build_config(
        self,
        node_def: NodeDefinition,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the synthetic ``node.config`` the handler reads.

        Handlers read config via ``XxxConfig.from_node(node)`` →
        ``getattr(node, "config")``. A builtin business-node definition carries
        its authored handler config (``expression``/``data``/``variables``/…) in
        ``locked_defaults``. Mirror the dispatch precedence
        ``user_input < mapped < locked_defaults``; ``parse_config`` keeps only
        the keys each node's spec declares and ignores the rest.
        """
        mapped: dict[str, Any] = {}
        if node_def.input_mapping and self._resolver is not None:
            try:
                mapped = await self._resolver.resolve_mapping(
                    node_def.input_mapping, {"custom": input_data},
                )
            except Exception:
                mapped = {}
        return {**input_data, **mapped, **node_def.locked_defaults}
