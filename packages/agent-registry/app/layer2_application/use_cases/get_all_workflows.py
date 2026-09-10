"""UC-GetAllWorkflows: Get All Workflows use case."""

from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository


class GetAllWorkflowsUseCase:
    """Use case for retrieving all workflows from the registry.
    
    This is a simple read operation that returns all workflows
    without any filtering or complex logic.
    """

    def __init__(self, repository: IWorkflowRepository):
        """Initialize use case with repository dependency.
        
        Args:
            repository: Workflow repository port implementation
        """
        self.repository = repository
    
    def execute(self) -> list[WorkflowResponseDTO]:
        """Get all workflows from the registry.
        
        Returns:
            List of all workflows as DTOs
        """
        # Fetch all workflows
        workflows = self.repository.find_all()

        # Convert to DTOs
        return WorkflowResponseDTO.from_entities(workflows)
