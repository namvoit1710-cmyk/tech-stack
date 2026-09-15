"""Tests for WorkflowNode, WorkflowEdge, and Workflow entity fields."""

from __future__ import annotations

import pytest

from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.agent_ref import AgentRef
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun


class TestWorkflowNodeFields:
    def test_defaults(self):
        node = WorkflowNode(id="n1", name="Task", node_type=NodeType.TASK)
        assert node.node_class == ""
        assert node.description == ""
        assert node.icon == ""
        assert node.color == ""
        assert node.tags == []
        assert node.in_ports == []
        assert node.out_ports == []
        assert node.action == ""
        assert node.instruction == ""
        assert node.agents == []

    def test_fields_set(self):
        node = WorkflowNode(
            id="n2", name="Fetch", node_type=NodeType.TASK,
            node_class="TECHNICAL", description="Calls API",
            icon="Globe", color="#3B82F6",
            tags=["http", "api"],
            in_ports=[{"id": "in_default", "label": "Input"}],
            out_ports=[{"id": "success", "label": "Success"}],
        )
        assert node.node_class == "TECHNICAL"
        assert node.icon == "Globe"
        assert node.color == "#3B82F6"
        assert len(node.tags) == 2
        assert node.in_ports[0]["id"] == "in_default"
        assert node.out_ports[0]["id"] == "success"

    def test_type_coercion(self):
        node = WorkflowNode(id="x", node_type="TRIGGER")  # type: ignore[arg-type]
        assert node.node_type == NodeType.TRIGGER


class TestWorkflowNodeAgentSupport:
    """SA-517: Tests for WorkflowNode agent support fields."""

    def test_action_field(self):
        """Node action field should be stored correctly."""
        node = WorkflowNode(
            id="n1", name="Summarize", node_type=NodeType.TASK,
            action="summarize_document"
        )
        assert node.action == "summarize_document"

    def test_instruction_field(self):
        """Node instruction field should be stored correctly."""
        node = WorkflowNode(
            id="n1", name="Task", node_type=NodeType.TASK,
            instruction="Summarize the document at {{$input.doc}}"
        )
        assert node.instruction == "Summarize the document at {{$input.doc}}"

    def test_agents_with_agentref_objects(self):
        """Node agents field should accept AgentRef objects."""
        agents = [
            AgentRef(agent_id="agent-summarizer", role="primary", priority=10),
            AgentRef(agent_id="agent-fallback", priority=1),
        ]
        node = WorkflowNode(
            id="n1", name="Task", node_type=NodeType.TASK,
            agents=agents
        )
        assert len(node.agents) == 2
        assert isinstance(node.agents[0], AgentRef)
        assert node.agents[0].agent_id == "agent-summarizer"
        assert node.agents[0].role == "primary"
        assert node.agents[0].priority == 10

    def test_agents_with_dict_conversion(self):
        """Node agents field should convert dicts to AgentRef objects."""
        agents_data = [
            {"agent_id": "agent-summarizer", "role": "primary", "priority": 10},
            {"agent_id": "agent-fallback", "priority": 1},
        ]
        node = WorkflowNode(
            id="n1", name="Task", node_type=NodeType.TASK,
            agents=agents_data
        )
        assert len(node.agents) == 2
        assert isinstance(node.agents[0], AgentRef)
        assert node.agents[0].agent_id == "agent-summarizer"
        assert node.agents[1].role == ""  # default applied

    def test_empty_agents_means_unrestricted(self):
        """Empty agents list means no constraint (any agent eligible)."""
        node = WorkflowNode(id="n1", name="Task", node_type=NodeType.TASK, agents=[])
        assert node.agents == []
        # This is the semantic: empty list = unrestricted

    def test_node_agents_is_strict_allowlist(self):
        """Non-empty agents list is strict allow-list."""
        node = WorkflowNode(
            id="n1", name="Task", node_type=NodeType.TASK,
            agents=[{"agent_id": "agent-only-one"}]
        )
        assert len(node.agents) == 1
        assert node.agents[0].agent_id == "agent-only-one"
        # Only "agent-only-one" is eligible for this node

    def test_full_node_with_all_agent_fields(self):
        """Node with all SA-517 fields should serialize correctly."""
        node = WorkflowNode(
            id="summarize-thread",
            name="Summarize Thread",
            node_type=NodeType.TASK,
            node_class="BUSINESS",
            description="Produces a three-sentence summary.",
            action="summarize_document",
            instruction="Summarize the ticket thread at {{$fetch-ticket.response_body.thread}}",
            agents=[{"agent_id": "agent-summarizer-gpt4o", "priority": 10}],
            worker_type="agent-worker",
        )
        assert node.action == "summarize_document"
        assert "{{$fetch-ticket" in node.instruction
        assert len(node.agents) == 1
        assert node.agents[0].agent_id == "agent-summarizer-gpt4o"


class TestWorkflowEdgeFields:
    def test_defaults(self):
        edge = WorkflowEdge(source_node_id="a", target_node_id="b")
        assert edge.source_port_id == ""
        assert edge.target_port_id == ""
        assert edge.label == ""

    def test_fields_set(self):
        edge = WorkflowEdge(
            source_node_id="a", target_node_id="b",
            source_port_id="out_default", target_port_id="in_default",
            label="on success",
        )
        assert edge.source_port_id == "out_default"
        assert edge.target_port_id == "in_default"
        assert edge.label == "on success"

    def test_condition(self):
        edge = WorkflowEdge(source_node_id="a", target_node_id="b", condition="then")
        assert edge.condition == "then"


class TestWorkflowFields:
    def test_defaults(self):
        wf = Workflow(id="wf-1", name="Test")
        assert wf.schema_version == "1.0.0"
        # SA-517: New fields should have defaults
        assert wf.action == ""
        assert wf.instruction == ""
        assert wf.agents == []

    def test_fields_set(self):
        wf = Workflow(id="wf-2", name="WF", schema_version="1.0.0")
        assert wf.schema_version == "1.0.0"


class TestWorkflowAgentSupport:
    """SA-517: Tests for Workflow agent support fields."""

    def test_workflow_action_field(self):
        """Workflow action field should be stored correctly."""
        wf = Workflow(id="wf-1", name="Customer Follow-up", action="customer_followup")
        assert wf.action == "customer_followup"

    def test_workflow_instruction_field(self):
        """Workflow instruction field should be stored correctly."""
        wf = Workflow(
            id="wf-1", name="Customer Follow-up",
            instruction="Handle support tickets marked 'awaiting-followup'."
        )
        assert wf.instruction == "Handle support tickets marked 'awaiting-followup'."

    def test_workflow_agents_with_agentref_objects(self):
        """Workflow agents field should accept AgentRef objects."""
        agents = [
            AgentRef(agent_id="agent-support-writer-v2", role="primary", priority=10),
            AgentRef(agent_id="agent-support-writer-v1", role="fallback", priority=1),
        ]
        wf = Workflow(id="wf-1", name="Test", agents=agents)
        assert len(wf.agents) == 2
        assert isinstance(wf.agents[0], AgentRef)
        assert wf.agents[0].agent_id == "agent-support-writer-v2"

    def test_workflow_agents_with_dict_conversion(self):
        """Workflow agents field should convert dicts to AgentRef objects."""
        agents_data = [
            {"agent_id": "agent-primary", "role": "primary", "priority": 10},
            {"agent_id": "agent-fallback", "priority": 1},
        ]
        wf = Workflow(id="wf-1", name="Test", agents=agents_data)
        assert len(wf.agents) == 2
        assert isinstance(wf.agents[0], AgentRef)
        assert wf.agents[0].agent_id == "agent-primary"

    def test_workflow_agents_independent_from_nodes(self):
        """Workflow agents should NOT cascade to nodes - they are independent."""
        workflow_agents = [{"agent_id": "workflow-agent"}]
        node_agents = [{"agent_id": "node-agent"}]
        
        wf = Workflow(
            id="wf-1", name="Test",
            agents=workflow_agents,
            nodes=[
                WorkflowNode(
                    id="n1", name="Task", node_type=NodeType.TASK,
                    agents=node_agents
                )
            ]
        )
        
        # Workflow-level agents
        assert len(wf.agents) == 1
        assert wf.agents[0].agent_id == "workflow-agent"
        
        # Node-level agents (independent, no inheritance)
        assert len(wf.nodes[0].agents) == 1
        assert wf.nodes[0].agents[0].agent_id == "node-agent"
        
        # Node does NOT inherit from workflow
        assert wf.nodes[0].agents[0].agent_id != wf.agents[0].agent_id

    def test_full_workflow_reference_example(self):
        """Test with the reference example from SA-517 spec."""
        wf = Workflow(
            id="wf-customer-followup",
            name="Customer Follow-up",
            action="customer_followup",
            instruction="Pick up support tickets marked 'awaiting-followup', draft a reply tailored to the customer's last message, and queue it for human review.",
            agents=[
                {"agent_id": "agent-support-writer-v2", "role": "primary", "priority": 10},
                {"agent_id": "agent-support-writer-v1", "role": "fallback", "priority": 1},
            ],
            nodes=[
                WorkflowNode(
                    id="summarize-thread",
                    name="Summarize Thread",
                    node_type=NodeType.TASK,
                    node_class="BUSINESS",
                    description="Produces a three-sentence summary of the ticket thread.",
                    action="summarize_document",
                    instruction="Summarize the ticket thread at {{$fetch-ticket.response_body.thread}} in at most three sentences.",
                    agents=[{"agent_id": "agent-summarizer-gpt4o", "priority": 10}],
                    worker_type="agent-worker",
                ),
                WorkflowNode(
                    id="draft-reply",
                    name="Draft Reply",
                    node_type=NodeType.TASK,
                    node_class="BUSINESS",
                    description="Drafts the follow-up reply.",
                    action="draft_customer_reply",
                    instruction="Using the summary at {{$summarize-thread.output.result}}, draft a reply.",
                    agents=[],  # Empty = unrestricted, any agent eligible
                    worker_type="agent-worker",
                ),
            ],
        )
        
        # Workflow-level fields
        assert wf.action == "customer_followup"
        assert "awaiting-followup" in wf.instruction
        assert len(wf.agents) == 2
        assert wf.agents[0].role == "primary"
        
        # First node: strict allow-list
        node1 = wf.nodes[0]
        assert node1.action == "summarize_document"
        assert len(node1.agents) == 1  # Only one agent allowed
        assert node1.agents[0].agent_id == "agent-summarizer-gpt4o"
        
        # Second node: empty agents = unrestricted
        node2 = wf.nodes[1]
        assert node2.action == "draft_customer_reply"
        assert node2.agents == []  # Any registered agent eligible


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing workflows."""

    def test_workflow_without_agent_fields_uses_defaults(self):
        """Existing workflow without SA-517 fields should get defaults."""
        # Simulate loading an old workflow without new fields
        wf = Workflow(
            id="old-workflow",
            name="Legacy Workflow",
            description="Created before SA-517",
            nodes=[
                WorkflowNode(
                    id="n1", name="Old Task", node_type=NodeType.TASK,
                    worker_type="http-request-worker"
                )
            ]
        )
        
        # All SA-517 fields should have safe defaults
        assert wf.action == ""
        assert wf.instruction == ""
        assert wf.agents == []
        
        assert wf.nodes[0].action == ""
        assert wf.nodes[0].instruction == ""
        assert wf.nodes[0].agents == []

    def test_node_from_dict_without_agent_fields(self):
        """Node created from dict without SA-517 fields should work."""
        node_data = {
            "id": "n1",
            "name": "Old Node",
            "node_type": "TASK",
            "worker_type": "http-worker",
        }
        node = WorkflowNode(**node_data)
        
        assert node.action == ""
        assert node.instruction == ""
        assert node.agents == []

    def test_workflow_from_dict_without_agent_fields(self):
        """Workflow from dict without SA-517 fields should load with defaults."""
        # This simulates deserializing a workflow stored before SA-517
        wf_data = {
            "id": "wf-old",
            "name": "Old Workflow",
            "description": "Before agent support",
            "nodes": [
                {"id": "n1", "name": "Task", "node_type": "TASK", "worker_type": "http-worker"}
            ],
            "edges": [],
        }
        
        nodes = [WorkflowNode(**n) for n in wf_data.get("nodes", [])]
        wf = Workflow(
            id=wf_data["id"],
            name=wf_data["name"],
            description=wf_data["description"],
            nodes=nodes,
        )
        
        # New fields should have defaults
        assert wf.action == ""
        assert wf.instruction == ""
        assert wf.agents == []
        assert wf.nodes[0].action == ""
        assert wf.nodes[0].agents == []


class TestInputSchemaDefaultRoundTrip:
    """Top-level ``default`` on input_schema items must survive ORM round-trip."""

    def test_top_level_default_preserved(self):
        from app.layer4_frameworks.orm.entities.workflow_entity import (
            WorkflowORMEntity,
        )
        schema = [
            {
                "key": "tblProcess",
                "outputType": "array",
                "default": [
                    {"stepId": 1, "description": "first"},
                    {"stepId": 2, "description": "second"},
                ],
                "fieldConfig": {"fieldWrapper": "TableWrapper"},
            },
            {
                "key": "name",
                "outputType": "string",
                "default": "alice",
                "fieldConfig": {},
            },
        ]
        wf = Workflow(id="wf1", name="W", input_schema=schema)
        orm = WorkflowORMEntity.from_domain(wf)
        restored = orm.to_domain()
        assert restored.input_schema == schema
class TestWorkflowRunRootRunId:
    def test_top_level_run_root_run_id_defaults_to_self_id(self):
        run = WorkflowRun(workflow_id="wf-1")
        assert run.root_run_id == run.id

    def test_top_level_run_with_explicit_id_root_matches(self):
        run = WorkflowRun(id="run-abc", workflow_id="wf-1")
        assert run.root_run_id == "run-abc"

    def test_explicit_root_run_id_preserved(self):
        run = WorkflowRun(workflow_id="wf-1", root_run_id="root-xyz")
        assert run.root_run_id == "root-xyz"

    def test_child_run_inherits_root_run_id(self):
        run = WorkflowRun(
            workflow_id="wf-child",
            parent_run_id="parent-1",
            root_run_id="root-top",
            depth_level=1,
        )
        assert run.root_run_id == "root-top"
        assert run.parent_run_id == "parent-1"

    def test_predefined_id_resync_pattern(self):
        """Mirrors start_run_use_case.py: id reassigned after __post_init__ → re-sync."""
        run = WorkflowRun(workflow_id="wf-1")
        run.id = "predefined-id"
        # Use-case logic re-syncs only for top-level runs.
        if run.parent_run_id is None:
            run.root_run_id = run.id
        assert run.root_run_id == "predefined-id"
