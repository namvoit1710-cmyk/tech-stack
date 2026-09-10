"""Gap-fill unit tests for layer1 domain entities (pure, no mocks).

Targets the uncovered domain branches from the 2026-07-02 sweep:

Agent  (agent.py): 221 validate_kind raise, 230 validate_status raise,
    239 validate_config_type raise, 258 validate_endpoint host-None,
    360 update_attribute invalid field, 463 create self-attach,
    504 create with DELETED status.
Tool   (tool.py): 74 validate_status raise, 83 validate_protocol raise,
    92/94 validate_name empty/too-long, 109 update_attribute invalid field,
    112/114 update_attribute name/endpoint normalization.
Workflow (workflow.py): 49 equals_content vs non-Workflow, 93
    update_attribute invalid field, 97 update_attribute no-op None.

Every raised type/message is grounded in the source, not convention.
"""

import pytest

from app.layer1_domain.entities.agent import (
    Agent,
    AgentKind,
    AgentStatus,
    ConfigType,
)
from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import (
    InvalidAgentConfigTypeException,
    InvalidAgentKindException,
    InvalidAgentStatusException,
    InvalidDataException,
)


# --------------------------------------------------------------------------- #
# Agent entity
# --------------------------------------------------------------------------- #
class TestAgentEntityGapFill:
    def _agent(self, sample_agent_data):
        return Agent.create(**sample_agent_data)

    # 221: validate_kind raises when kind is not a valid AgentKind
    def test_validate_kind_rejects_non_enum(self, sample_agent_data):
        agent = self._agent(sample_agent_data)
        agent.kind = "not-a-kind"
        with pytest.raises(InvalidAgentKindException):
            agent.validate_kind()

    # 230: validate_status raises when status is not a valid AgentStatus
    def test_validate_status_rejects_non_enum(self, sample_agent_data):
        agent = self._agent(sample_agent_data)
        agent.status = "not-a-status"
        with pytest.raises(InvalidAgentStatusException):
            agent.validate_status()

    # 239: validate_config_type raises when config_type is not a valid ConfigType
    def test_validate_config_type_rejects_non_enum(self, sample_agent_data):
        agent = self._agent(sample_agent_data)
        agent.config_type = "not-a-config"
        with pytest.raises(InvalidAgentConfigTypeException):
            agent.validate_config_type()

    # 258: validate_endpoint raises when a URL has scheme+netloc but no host
    def test_validate_endpoint_rejects_url_without_host(self, sample_agent_data):
        agent = self._agent(sample_agent_data)
        agent.invoke_endpoint = "https://user@"  # netloc truthy, hostname None
        with pytest.raises(InvalidDataException) as exc:
            agent.validate_endpoint()
        assert "valid host" in str(exc.value)

    # 360: update_attribute raises on an unknown field name
    def test_update_attribute_rejects_unknown_field(self, sample_agent_data):
        agent = self._agent(sample_agent_data)
        with pytest.raises(InvalidDataException) as exc:
            agent.update_attribute("no_such_field", "x")
        assert "Invalid field name" in str(exc.value)

    # 463: create rejects an agent that lists itself in its child agents
    def test_create_rejects_self_attachment(self, sample_agent_data):
        agent_id = sample_agent_data["id"]
        data = dict(sample_agent_data)
        data["agents"] = [agent_id]
        with pytest.raises(InvalidDataException) as exc:
            Agent.create(**data)
        assert "cannot attach itself" in str(exc.value)

    # 504: create rejects DELETED status (new agent cannot be deleted)
    def test_create_rejects_deleted_status(self, sample_agent_data):
        data = dict(sample_agent_data)
        data["status"] = AgentStatus.DELETED
        with pytest.raises(InvalidAgentStatusException):
            Agent.create(**data)


# --------------------------------------------------------------------------- #
# Tool entity
# --------------------------------------------------------------------------- #
class TestToolEntityGapFill:
    def _tool(self, sample_tool_data):
        return Tool(**sample_tool_data)

    # 74: validate_status raises on a non-ToolStatus value
    def test_validate_status_rejects_non_enum(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        tool.status = "bogus"
        with pytest.raises(InvalidDataException) as exc:
            tool.validate_status()
        assert "Invalid tool status" in str(exc.value)

    # 83: validate_protocol raises on a non-ToolProtocol value
    def test_validate_protocol_rejects_non_enum(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        tool.protocol = "carrier-pigeon"
        with pytest.raises(InvalidDataException) as exc:
            tool.validate_protocol()
        assert "Invalid tool protocol" in str(exc.value)

    # 92: validate_name rejects empty
    def test_validate_name_rejects_empty(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        tool.name = "   "
        with pytest.raises(InvalidDataException) as exc:
            tool.validate_name()
        assert "cannot be empty" in str(exc.value)

    # 94: validate_name rejects > 255 chars (boundary)
    def test_validate_name_rejects_too_long(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        tool.name = "a" * 256
        with pytest.raises(InvalidDataException) as exc:
            tool.validate_name()
        assert "cannot exceed 255 characters" in str(exc.value)

    # 109: update_attribute raises on unknown field
    def test_update_attribute_rejects_unknown_field(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        with pytest.raises(InvalidDataException) as exc:
            tool.update_attribute("no_such_field", "x")
        assert "Invalid field name" in str(exc.value)

    # 112: update_attribute strips a name value before comparison/set
    def test_update_attribute_strips_name(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        changed = tool.update_attribute("name", "  renamed-tool  ")
        assert changed is True
        assert tool.name == "renamed-tool"

    # 114: update_attribute normalizes a blank endpoint ("   ".strip() or None -> None).
    # Source line 116 then treats None as a no-op and returns False WITHOUT mutating
    # the endpoint (the pre-existing URL is preserved). Expectation grounded in source.
    def test_update_attribute_blank_endpoint_is_noop(self, sample_tool_data):
        tool = self._tool(sample_tool_data)
        original_endpoint = tool.endpoint
        changed = tool.update_attribute("endpoint", "   ")
        assert changed is False
        assert tool.endpoint == original_endpoint


# --------------------------------------------------------------------------- #
# Workflow entity
# --------------------------------------------------------------------------- #
class TestWorkflowEntityGapFill:
    def _workflow(self, sample_workflow_data):
        return Workflow(**sample_workflow_data)

    # 49: equals_content returns False against a non-Workflow object
    def test_equals_content_false_for_non_workflow(self, sample_workflow_data):
        workflow = self._workflow(sample_workflow_data)
        assert workflow.equals_content("not a workflow") is False

    # 93: update_attribute raises on unknown field
    def test_update_attribute_rejects_unknown_field(self, sample_workflow_data):
        workflow = self._workflow(sample_workflow_data)
        with pytest.raises(InvalidDataException) as exc:
            workflow.update_attribute("no_such_field", "x")
        assert "Invalid field name" in str(exc.value)

    # 97: update_attribute is a no-op (returns False) when value is None
    def test_update_attribute_noop_on_none(self, sample_workflow_data):
        workflow = self._workflow(sample_workflow_data)
        original_name = workflow.name
        changed = workflow.update_attribute("name", None)
        assert changed is False
        assert workflow.name == original_name
