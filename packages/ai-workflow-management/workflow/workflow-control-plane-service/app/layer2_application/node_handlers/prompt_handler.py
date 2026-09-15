"""PROMPT node handler: resolve the configured text and emit it as output.

A passive data feed. The ``content`` field may contain ``{{...}}``
expressions referencing upstream node outputs, variables, or metadata;
the resolver expands them, and the resolved string is published under the
node's own output namespace so a downstream LLM/agent node can read it via
``{{$<nodeId>.output.content}}``. The node never calls an LLM itself.
"""

from __future__ import annotations

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.events import AiFeedProduced
from app.layer1_domain.node_spec.specs import PromptConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver

# Agent runs declare ``{"source": "agent"}`` in ``run.metadata`` (captured at
# ``POST /runs``). The ``AiFeedProduced`` event fires only for those.
_AGENT_SOURCE = "agent"


class PromptNodeHandler(AsyncInlineHandlerBase):
    """Resolves ``config.content`` and emits the resolved string."""

    def __init__(self, variable_resolver: VariableResolver) -> None:
        self._resolver = variable_resolver

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
        result.output_port = "out_default"
        result.completion_mode = "immediate"

        cfg = PromptConfig.from_node(node)
        resolved = await self._resolver.resolve(cfg.content, run.context.variables)
        resolved_str = str(resolved)

        result.state_updates[node.id] = {
            "output": {"content": resolved_str},
            "metadata": {"type": "prompt"},
        }

        # Agent-feed signal: on agent-initiated runs, emit ``AiFeedProduced``
        # so the prompt feed rides the standard event spine — exactly like
        # ``NodeInput`` / ``EdgeTraversed``. It is left with empty ``metadata``
        # so the outermost ``MetadataStampingPublisher`` stamps the run's
        # metadata (carrying ``source: "agent"``); that is what routes the
        # event into the execution log AND forwards it to the centralized
        # ``WORKFLOW_EVENT_TOPIC`` for agent consumers. No custom per-node
        # ``target_topic`` — that diverted the event off the centralized stream
        # and left a gap in the per-run sequence.
        #
        # The event is wire-thin: it carries NO inline content. ``resolved_str``
        # can exceed the broker's ~1 MB cap, so it is file-backed via the
        # ``state_updates`` above — the engine uploads it in
        # ``apply_state_updates_as_files`` and the node's ``NodeOutputProduced``
        # carries the file_ref pointer. The agent materialises the full content
        # from the file service on demand, correlating on ``(run_id, node_id)``.
        # No-op for non-agent runs.
        run_meta = run.metadata or {}
        if run_meta.get("source") == _AGENT_SOURCE:
            result.events.append(
                AiFeedProduced(
                    run_id=run.id,
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_name=node.name,
                    root_run_id=run.root_run_id or run.id,
                    depth=run.depth_level,
                    source="workflow-control-plane",
                )
            )
        return result
