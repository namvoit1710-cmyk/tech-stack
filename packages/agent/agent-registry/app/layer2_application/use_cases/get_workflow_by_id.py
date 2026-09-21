"""UC: GetWorkflowById use case."""

from app.layer2_application.dtos.workflow_response_dto import WorkflowResponseDTO
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository

class GetWorkflowByIdUseCase:
    """Use case for retrieving a workflow by its ID."""
    
    def __init__(self, repository: IWorkflowRepository):
        self.repository = repository
    
    def execute(self, workflow_id: str) -> WorkflowResponseDTO:
        """Execute the use case to get a workflow by ID.
        
        Args:
            workflow_id (str): The ID of the workflow to retrieve.
        
        Returns:
            WorkflowResponseDTO: The DTO representing the workflow.
        
        Raises:
            NotFoundException: If the workflow with the given ID is not found.
        """
        workflow = self.repository.find_by_id(workflow_id)
        if not workflow:
            raise NotFoundException("Workflow", entity_id=workflow_id)
        return WorkflowResponseDTO.from_entity(workflow)