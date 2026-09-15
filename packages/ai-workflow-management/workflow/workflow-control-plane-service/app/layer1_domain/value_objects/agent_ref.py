"""AgentRef value object for workflow and node agent allow-lists.

AgentRef represents a reference to an agent that is permitted to execute
a workflow node or an entire workflow (when invoked as a sub-workflow).

Attributes
----------
agent_id : str
    Stable identifier of the agent. Matches the agent-worker's registered
    identity. Use the Agent type field from the Agent List API as the key.
    Required — must be a non-empty string.

role : str
    Human-readable role label (e.g., "primary", "reviewer", "fallback").
    Presentational only — runtime does not use this for logic. Defaults to "".

priority : int
    Higher value = ranked first when the agent-selection layer picks among
    eligible agents. Defaults to 0. Negative values are allowed.

Example
-------
>>> ref = AgentRef(agent_id="agent-summarizer-gpt4o", role="primary", priority=10)
>>> ref.to_dict()
{'agent_id': 'agent-summarizer-gpt4o', 'role': 'primary', 'priority': 10}
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AgentRefValidationError(ValueError):
    """Raised when AgentRef fails validation."""
    pass


@dataclass
class AgentRef:
    """Reference to an agent identity permitted to execute a node or workflow."""

    agent_id: str
    role: str = ""
    priority: int = 0

    def __post_init__(self) -> None:
        """Validate fields after initialization."""
        # agent_id must be a non-empty string
        if not isinstance(self.agent_id, str):
            raise AgentRefValidationError(
                f"agent_id must be a string, got {type(self.agent_id).__name__}"
            )
        if not self.agent_id or not self.agent_id.strip():
            raise AgentRefValidationError("agent_id must be a non-empty string")

        # role can be any string (including empty)
        if not isinstance(self.role, str):
            raise AgentRefValidationError(
                f"role must be a string, got {type(self.role).__name__}"
            )

        # priority must be an integer (not float, not bool in Python semantics)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise AgentRefValidationError(
                f"priority must be an integer, got {type(self.priority).__name__}"
            )

    def to_dict(self) -> dict:
        """Serialize to a dictionary for JSON storage."""
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentRef":
        """Deserialize from a dictionary.

        Applies defaults for missing optional fields and validates.

        Parameters
        ----------
        data : dict
            Dictionary with at least ``agent_id`` key.

        Returns
        -------
        AgentRef
            A validated AgentRef instance.

        Raises
        ------
        AgentRefValidationError
            If validation fails.
        """
        return cls(
            agent_id=data.get("agent_id", ""),  # will fail validation if empty
            role=data.get("role", ""),
            priority=data.get("priority", 0),
        )
