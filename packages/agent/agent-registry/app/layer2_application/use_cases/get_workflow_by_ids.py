"""UC-GetWorkflowsByIds: Get Workflows by IDs use case."""

from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository


class GetWorkflowsByIdsUseCase:
    """Use case for retrieving workflows by IDs from the registry.
    
    This is a simple read operation that returns workflows
    based on provided IDs without any complex logic.
    """

    def __init__(self, repository: IWorkflowRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Workflow repository port implementation
        """
        self.repository = repository
    
    def execute(self, ids: list[str]) -> list[WorkflowResponseDTO]:
        """Get workflows by IDs from the registry.
        
        Args:
            ids: List of workflow IDs to retrieve
            
        Returns:
            List of workflows as DTOs
        """
        # Fetch workflows by IDs
        workflows = self.repository.find_by_ids(ids)

        # Convert to DTOs
        return [WorkflowResponseDTO.from_entity(workflow) for workflow in workflows]
