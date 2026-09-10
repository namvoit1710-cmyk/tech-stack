"""Agent repository port - interface for agent persistence operations.

This is a Protocol (interface) that defines what the application layer needs.
Infrastructure layer will implement this interface.
"""

from typing import Protocol
from datetime import datetime
from app.layer1_domain.entities.agent import Agent, AgentKind, AgentStatus


class IAgentRepository(Protocol):
    """Interface for agent repository operations.
    
    This port defines the contract for agent persistence.
    Infrastructure layer provides the implementation.
    """

    def save(self, agent: Agent, tool_ids: list[str] | None = None, workflow_ids: list[str] | None = None) -> None:
        """Save a new agent to the repository.
        
        Args:
            agent: Agent entity to save
            tool_ids: Optional list of tool IDs to link to the agent
            workflow_ids: Optional list of workflow IDs to link to the agent
        Raises:
            AlreadyExistsException: If an agent with the same name and version already exists
            InvalidDataException: If saving fails due to invalid data
        """
        ...
        
    def update_health_status(self, agent_id: str, is_alive: bool, last_health_check_at: datetime | None = None) -> None:
        """Update the health status of an agent.
        
        Args:
            agent_id: ID of agent to update
            is_alive: Liveness status to set
            last_health_check_at: Optional timestamp of the last health check
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        ...

    def update(
        self,
        agent: Agent,
        tool_ids: list[str] | None = None,
        workflow_ids: list[str] | None = None,
    ) -> None:
        """Update an existing agent in the repository.
        
        Args:
            agent: Agent entity to update
            tool_ids: Optional full replacement list of tool IDs to link to the agent
            workflow_ids: Optional full replacement list of workflow IDs to link to the agent
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        ...

    def soft_delete(self, agent_id: str) -> None:
        """Mark agent as deleted.
        
        Args:
            agent_id: ID of agent to delete
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        ...

    def find_by_id(self, agent_id: str) -> Agent | None:
        """Find an agent by ID.
        
        Args:
            agent_id: ID of agent to find
            
        Returns:
            Agent entity if found, None otherwise
        """
        ...
        
    def find_by_ids(self, agent_ids: list[str]) -> list[Agent]:
        """Find multiple agents by their IDs.
        
        Args:
            agent_ids: List of agent IDs to find
            
        Returns:
            List of Agent entities matching the provided IDs

        Raises:
            NotFoundException: If any requested agent ID does not exist
        """
        ...

    def find_by_name(self, name: str) -> Agent | None:
        """Find an agent by name.

        Args:
            name: Name of agent to find

        Returns:
            Agent entity if found, None otherwise
        """
        ...

    def find_by_name_and_version(self, name: str, version: str) -> Agent | None:
        """Find an agent by exact (name, version), including soft-deleted rows.

        Matches the uq_agents_name_version unique constraint so callers can pre-check for a
        conflicting agent before performing side effects (e.g. persisting workflows).

        Returns:
            Agent entity if a row with that (name, version) exists, None otherwise
        """
        ...

    def find_by_criteria(
        self,
        agent_id: str | None = None,
        status: AgentStatus | None = None,
        kind: AgentKind | None = None,
        is_alive: bool | None = None,
        is_published: bool | None = None,
        user_email: str | None = None,
    ) -> list[Agent]:
        """Find agents by various criteria.

        Args:
            agent_id: Filter by agent ID
            status: Filter by agent status
            kind: Filter by agent kind (business/technical)
            is_alive: Filter by liveness status
            is_published: Filter by published status
            user_email: Filter by owner (the user_email captured at registration)

        Returns:
            List of agents matching criteria
        """
        ...

    def find_all(self) -> list[Agent]:
        """Retrieve all agents from the repository.

        Returns:
            List of all agents
        """
        ...


        
    def find_deleted_agents(self) -> list[Agent]:
        """Find agents that are marked as deleted.
        
        Returns:
            List of deleted agents
        """
        ...

    # Junction table operations for many-to-many relationships

    def add_tool(self, agent_id: str, tool_id: str) -> None:
        """Link a tool to an agent.

        Args:
            agent_id: ID of agent
            tool_id: ID of tool to link

        Raises:
            NotFoundException: If agent or tool doesn't exist
            RelationshipAlreadyExistsException: If link already exists
        """
        ...

    def remove_tool(self, agent_id: str, tool_id: str) -> None:
        """Unlink a tool from an agent.

        Args:
            agent_id: ID of agent
            tool_id: ID of tool to unlink

        Raises:
            NotFoundException: If agent or tool doesn't exist
            RelationshipNotFoundException: If link doesn't exist
        """
        ...

    def get_tools(self, agent_id: str) -> list[str]:
        """Get tool IDs linked to an agent.

        Args:
            agent_id: ID of agent

        Returns:
            List of tool IDs

        Raises:
            NotFoundException: If agent doesn't exist
        """
        ...

    def add_workflow(self, agent_id: str, workflow_id: str) -> None:
        """Link a workflow to an agent.

        Args:
            agent_id: ID of agent
            workflow_id: ID of workflow to link

        Raises:
            NotFoundException: If agent or workflow doesn't exist
            RelationshipAlreadyExistsException: If link already exists
        """
        ...

    def remove_workflow(self, agent_id: str, workflow_id: str) -> None:
        """Unlink a workflow from an agent.

        Args:
            agent_id: ID of agent
            workflow_id: ID of workflow to unlink

        Raises:
            NotFoundException: If agent or workflow doesn't exist
            RelationshipNotFoundException: If link doesn't exist
        """
        ...

    def get_workflows(self, agent_id: str) -> list[str]:
        """Get workflow IDs linked to an agent.

        Args:
            agent_id: ID of agent

        Returns:
            List of workflow IDs

        Raises:
            NotFoundException: If agent doesn't exist
        """
        ...
