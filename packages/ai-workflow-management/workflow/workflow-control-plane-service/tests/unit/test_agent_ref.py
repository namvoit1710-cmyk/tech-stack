"""Unit tests for AgentRef value object (SA-517)."""

from __future__ import annotations

import pytest

from app.layer1_domain.value_objects.agent_ref import AgentRef, AgentRefValidationError


class TestAgentRefCreation:
    """Tests for AgentRef creation and defaults."""

    def test_valid_creation(self):
        """AgentRef with valid agent_id should be created successfully."""
        ref = AgentRef(agent_id="agent-summarizer-gpt4o")
        assert ref.agent_id == "agent-summarizer-gpt4o"
        assert ref.role == ""  # default
        assert ref.priority == 0  # default

    def test_full_creation(self):
        """AgentRef with all fields should be created successfully."""
        ref = AgentRef(agent_id="agent-writer-v2", role="primary", priority=10)
        assert ref.agent_id == "agent-writer-v2"
        assert ref.role == "primary"
        assert ref.priority == 10

    def test_negative_priority(self):
        """AgentRef should accept negative priority values."""
        ref = AgentRef(agent_id="agent-fallback", priority=-5)
        assert ref.priority == -5

    def test_empty_role(self):
        """AgentRef should accept empty role."""
        ref = AgentRef(agent_id="agent-test", role="")
        assert ref.role == ""


class TestAgentRefValidation:
    """Tests for AgentRef validation rules."""

    def test_missing_agent_id_raises(self):
        """AgentRef with empty agent_id should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="agent_id must be a non-empty string"):
            AgentRef(agent_id="")

    def test_whitespace_agent_id_raises(self):
        """AgentRef with whitespace-only agent_id should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="agent_id must be a non-empty string"):
            AgentRef(agent_id="   ")

    def test_none_agent_id_raises(self):
        """AgentRef with None agent_id should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="agent_id must be a string"):
            AgentRef(agent_id=None)  # type: ignore

    def test_non_string_agent_id_raises(self):
        """AgentRef with non-string agent_id should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="agent_id must be a string"):
            AgentRef(agent_id=123)  # type: ignore

    def test_float_priority_raises(self):
        """AgentRef with float priority should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="priority must be an integer"):
            AgentRef(agent_id="agent-test", priority=1.5)  # type: ignore

    def test_bool_priority_raises(self):
        """AgentRef with bool priority should raise validation error (bool is not int for this use case)."""
        with pytest.raises(AgentRefValidationError, match="priority must be an integer"):
            AgentRef(agent_id="agent-test", priority=True)  # type: ignore

    def test_non_string_role_raises(self):
        """AgentRef with non-string role should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="role must be a string"):
            AgentRef(agent_id="agent-test", role=123)  # type: ignore


class TestAgentRefSerialization:
    """Tests for AgentRef to_dict and from_dict methods."""

    def test_to_dict(self):
        """AgentRef should serialize to a dictionary."""
        ref = AgentRef(agent_id="agent-summarizer", role="primary", priority=10)
        data = ref.to_dict()
        assert data == {
            "agent_id": "agent-summarizer",
            "role": "primary",
            "priority": 10,
        }

    def test_to_dict_defaults(self):
        """AgentRef with defaults should serialize correctly."""
        ref = AgentRef(agent_id="agent-test")
        data = ref.to_dict()
        assert data == {
            "agent_id": "agent-test",
            "role": "",
            "priority": 0,
        }

    def test_from_dict_full(self):
        """AgentRef should deserialize from a complete dictionary."""
        data = {"agent_id": "agent-writer", "role": "reviewer", "priority": 5}
        ref = AgentRef.from_dict(data)
        assert ref.agent_id == "agent-writer"
        assert ref.role == "reviewer"
        assert ref.priority == 5

    def test_from_dict_minimal(self):
        """AgentRef should deserialize from a minimal dictionary, applying defaults."""
        data = {"agent_id": "agent-minimal"}
        ref = AgentRef.from_dict(data)
        assert ref.agent_id == "agent-minimal"
        assert ref.role == ""
        assert ref.priority == 0

    def test_from_dict_missing_agent_id_raises(self):
        """AgentRef.from_dict with missing agent_id should raise validation error."""
        with pytest.raises(AgentRefValidationError, match="agent_id must be a non-empty string"):
            AgentRef.from_dict({})

    def test_roundtrip(self):
        """AgentRef should survive a to_dict -> from_dict roundtrip."""
        original = AgentRef(agent_id="agent-roundtrip", role="fallback", priority=-1)
        data = original.to_dict()
        restored = AgentRef.from_dict(data)
        assert restored.agent_id == original.agent_id
        assert restored.role == original.role
        assert restored.priority == original.priority
