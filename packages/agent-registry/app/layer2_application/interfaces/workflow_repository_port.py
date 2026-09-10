"""Workflow repository port - interface for workflow persistence operations.

This is a Protocol (interface) that defines what the application layer needs.
Infrastructure layer will implement this interface.
"""

from typing import Protocol

from app.layer1_domain.entities.workflow import Workflow


class IWorkflowRepository(Protocol):
    """Interface for workflow repository operations.

    This port defines the contract for workflow persistence.
    Infrastructure layer provides the implementation.
    """

    def save(self, workflow: Workflow) -> Workflow:
        """Save a new workflow to the repository.

        Args:
            workflow: Workflow entity to save

        Returns:
            The saved Workflow entity
        """
        ...
        
    def upsert(self, workflow: Workflow) -> Workflow:
        """Upsert a workflow in the repository.

        Args:
            workflow: Workflow entity to upsert

        Returns:
            The upserted Workflow entity
        """
        ...

    def update(self, workflow: Workflow) -> Workflow:
        """Update an existing workflow in the repository.

        Args:
            workflow: Workflow entity to update

        Returns:
            The updated Workflow entity

        Raises:
            NotFoundException: If workflow doesn't exist
        """
        ...

    def delete(self, workflow_id: str) -> None:
        """Delete a workflow from the repository.

        Args:
            workflow_id: ID of workflow to delete

        Raises:
            NotFoundException: If workflow doesn't exist
        """
        ...

    def find_by_id(self, workflow_id: str) -> Workflow | None:
        """Find a workflow by ID.

        Args:
            workflow_id: ID of workflow to find

        Returns:
            Workflow entity if found, None otherwise
        """
        ...
        
    def find_by_ids(self, workflow_ids: list[str]) -> list[Workflow]:
        """Find multiple workflows by their IDs.

        Args:
            workflow_ids: List of workflow IDs to find

        Returns:
            List of Workflow entities that match the given IDs

        Raises:
            NotFoundException: If any requested workflow ID does not exist
        """
        ...

    def find_by_name(self, name: str) -> Workflow | None:
        """Find a workflow by name.

        Args:
            name: Name of workflow to find

        Returns:
            Workflow entity if found, None otherwise
        """
        ...

    def find_by_version(self, version: str) -> list[Workflow]:
        """Find workflows by version.

        Args:
            version: Version to search for

        Returns:
            List of workflows with the given version
        """
        ...

    def find_all(self) -> list[Workflow]:
        """Retrieve all workflows from the repository.

        Returns:
            List of all workflows
        """
        ...
