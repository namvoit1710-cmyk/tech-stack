"""Unit tests for the PROMPT node handler.

PROMPT is a passive data feed: it resolves ``config.content`` (which may
contain ``{{...}}`` references) and emits the resolved string under the
node's own output namespace, on the single ``out_default`` port.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.events import (
    AiFeedProduced,
    EventTopic,
    topic_for_event,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.prompt_handler import (
    PromptNodeHandler,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)


def _resolver() -> VariableResolver:
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _topology() -> GraphTopology:
    return GraphTopology(
        edge_map={}, adjacency={}, predecessors={}, entry_nodes=[], leaf_nodes=[]
    )


class TestPromptHandler:
    @pytest.mark.asyncio
    async def test_literal_text_passthrough(self):
        handler = PromptNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="p1", node_type=NodeType.PROMPT,
            config={"content": "You are a helpful assistant."},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, _topology(), None, TaskFactory())

        assert result.output_port == "out_default"
        assert result.completion_mode == "immediate"
        assert result.state_updates["p1"]["output"]["content"] == "You are a helpful assistant."
        assert result.state_updates["p1"]["metadata"]["type"] == "prompt"

    @pytest.mark.asyncio
    async def test_resolves_template_reference(self):
        handler = PromptNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="p1", node_type=NodeType.PROMPT,
            config={"content": "Summarize: {{$src.output.text}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"src": {"output": {"text": "the quarterly report"}}}),
        )

        result = await handler.handle(node, run, workflow, _topology(), None, TaskFactory())

        assert result.state_updates["p1"]["output"]["content"] == "Summarize: the quarterly report"

    @pytest.mark.asyncio
    async def test_empty_content_emits_empty_string(self):
        handler = PromptNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(id="p1", node_type=NodeType.PROMPT, config={})
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, _topology(), None, TaskFactory())

        assert result.state_updates["p1"]["output"]["content"] == ""


class TestPromptHandlerAiFeed:
    """The ``AiFeedProduced`` event — agent runs only, routed via the
    centralized topic (no per-node custom ``target_topic``), wire-thin
    (no inline content)."""

    @pytest.mark.asyncio
    async def test_agent_run_emits_ai_feed_event(self):
        handler = PromptNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="p1", name="My Prompt", node_type=NodeType.PROMPT,
            config={"content": "Summarize: {{$src.output.text}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(
            id="r1", workflow_id="wf", metadata={"source": "agent"},
            context=RunContext(variables={"src": {"output": {"text": "the report"}}}),
        )

        result = await handler.handle(node, run, workflow, _topology(), None, TaskFactory())

        assert len(result.events) == 1
        event = result.events[0]
        assert isinstance(event, AiFeedProduced)
        # No per-node custom topic override: the event must ride the standard
        # spine so MetadataStampingPublisher stamps the run metadata and it
        # reaches the centralized topic + execution log like NodeInput.
        assert event.metadata == {}
        assert "target_topic" not in event.metadata
        # Wire-thin: NO inline content (prompt content can exceed the ~1 MB
        # broker cap). Only correlation identifiers ride the wire.
        assert not hasattr(event, "content")
        assert event.node_id == "p1"
        assert event.node_name == "My Prompt"
        assert event.run_id == "r1"
        assert event.workflow_id == "wf"
        assert event.root_run_id == "r1"
        assert event.source == "workflow-control-plane"
        # The resolved content is file-backed via state_updates — the engine
        # uploads it and NodeOutputProduced carries the file_ref pointer, which
        # the agent materialises on demand correlating by (run_id, node_id).
        assert result.state_updates["p1"]["output"]["content"] == "Summarize: the report"

    def test_event_routes_to_canonical_centralized_topic(self):
        """``AiFeedProduced`` is an enumerable event type resolving to the
        canonical ``ai.feed.produced`` topic — not a per-node topic."""
        assert EventTopic.AI_FEED_PRODUCED == "ai.feed.produced"
        assert topic_for_event(AiFeedProduced()) == "ai.feed.produced"

    @pytest.mark.asyncio
    async def test_non_agent_run_emits_no_feed(self):
        handler = PromptNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="p1", node_type=NodeType.PROMPT,
            config={"content": "hello"},
        )
        workflow = Workflow(id="wf", nodes=[node])

        # No metadata at all.
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())
        result = await handler.handle(node, run, workflow, _topology(), None, TaskFactory())
        assert result.events == []
        assert result.state_updates["p1"]["output"]["content"] == "hello"

        # Non-agent source.
        run_ui = WorkflowRun(
            id="r2", workflow_id="wf", metadata={"source": "ui"}, context=RunContext(),
        )
        result_ui = await handler.handle(node, run_ui, workflow, _topology(), None, TaskFactory())
        assert result_ui.events == []
