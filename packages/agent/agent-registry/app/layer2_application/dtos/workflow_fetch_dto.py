"""Workflow Fetch DTO - Input contract for fetching workflow data from external API."""
from dataclasses import dataclass
from app.layer1_domain.entities.workflow import Workflow
@dataclass
class WorkflowFetchDTO:
    """Data transfer object for fetching workflow data from external API."""
    id: str
    name: str
    description: str
    version: str
    status: str
    main_flow: bool
    input_schema: list | None = None
    output_schema: list | None = None
    metadata: dict | None = None
    
    @classmethod
    def from_api_response(cls, response: dict) -> "WorkflowFetchDTO":
        """Create DTO from API response data.
        
        Args:
            response: Dictionary with API response data
            
        Returns:
            WorkflowFetchDTO instance
        """
        return cls(
            id=response.get("id", ""),
            name=response.get("name", ""),
            description=response.get("description", ""),
            version=response.get("version", ""),
            status=response.get("status", ""),
            main_flow=response.get("main_flow", False),
            input_schema=response.get("input_schema", []),
            output_schema=response.get("output_schema", []),
            metadata=response.get("metadata", {})
        )
        
    def to_domain_entity(self) -> "Workflow":
        """Convert DTO to domain entity.
        
        Args:
            self: WorkflowFetchDTO instance
            
        Returns:
            Workflow domain entity
        """ 
        return Workflow.create(
            id=self.id,
            name=self.name,
            description=self.description,
            version=self.version,
            status=self.status,
            main_flow=self.main_flow,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            metadata=self.metadata
        )