from typing import Protocol

from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO


class IFetchWorkflowApiClient(Protocol):
    """Interface for fetching workflow data from an external API.
    
    This port defines the contract for interacting with the workflow API.
    Infrastructure layer provides the implementation (e.g., using HTTP client).
    """
    async def fetch_workflow(self, workflow_id: str) -> WorkflowFetchDTO:
        """Fetch workflow data from the external API.
        
        Returns:
            A workflow DTO
        Raises:
            InvalidDataException: If the workflow data returned from the external API is invalid or cannot be parsed
            NotFoundException: If the workflow with the given ID is not found in the external API
        """
        ...