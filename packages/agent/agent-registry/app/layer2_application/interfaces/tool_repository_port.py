"""Tool repository port - interface for tool persistence operations.

This is a Protocol (interface) that defines what the application layer needs.
Infrastructure layer will implement this interface.
"""

from typing import Protocol

from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus


class IToolRepository(Protocol):
    """Interface for tool repository operations.

    This port defines the contract for tool persistence.
    Infrastructure layer provides the implementation.
    """

    def save(self, tool: Tool) -> None:
        """Save a new tool to the repository.

        Args:
            tool: Tool entity to save
        """
        ...

    def update(self, tool: Tool) -> None:
        """Update an existing tool in the repository.

        Args:
            tool: Tool entity to update

        Raises:
            NotFoundException: If tool doesn't exist
        """
        ...

    def delete(self, tool_id: str) -> None:
        """Delete a tool from the repository.

        Args:
            tool_id: ID of tool to delete

        Raises:
            NotFoundException: If tool doesn't exist
        """
        ...

    def find_by_id(self, tool_id: str) -> Tool | None:
        """Find a tool by ID.

        Args:
            tool_id: ID of tool to find

        Returns:
            Tool entity if found, None otherwise
        """
        ...
        
    def find_by_ids(self, tool_ids: list[str]) -> list[Tool]:
        """Find multiple tools by their IDs.

        Args:
            tool_ids: List of tool IDs to find

        Returns:
            List of Tool entities that match the given IDs

        Raises:
            NotFoundException: If any requested tool ID does not exist
        """
        ...

    def find_by_name(self, name: str) -> Tool | None:
        """Find a tool by name.

        Args:
            name: Name of tool to find

        Returns:
            Tool entity if found, None otherwise
        """
        ...

    def find_by_protocol(self, protocol: ToolProtocol) -> list[Tool]:
        """Find tools by protocol.

        Args:
            protocol: Protocol to search for

        Returns:
            List of tools with the given protocol
        """
        ...

    def find_by_status(self, status: ToolStatus) -> list[Tool]:
        """Find tools by status.

        Args:
            status: Status to search for

        Returns:
            List of tools with the given status
        """
        ...

    def find_all(self) -> list[Tool]:
        """Retrieve all tools from the repository.

        Returns:
            List of all tools
        """
        ...
        
        
    def soft_delete(self, tool_id: str) -> None:
        """Soft delete a tool from the repository (mark as deleted).

        Args:
            tool_id: ID of tool to soft delete
        Raises:
            NotFoundException: If tool doesn't exist
        """
        ...