from abc import ABC, abstractmethod
from typing import Optional
from app.layer1_domain.entities.workflow_entity import Workflow, WorkNodeStatus, WorkflowRunInfoResponse
from app.layer1_domain.entities.workflow_node import ArtifactPayload, InputNodeResponse, NodePatchResponse, TaskNodeOutputResponse

class IWorkflowClient(ABC):

    # @abstractmethod
    # def get_all_by_agent_id(self, agent_id: str) -> list[Workflow]: ...

    @abstractmethod
    async def get_by_id(self, workflow_id: str) -> Optional[Workflow]: ...

    @abstractmethod
    async def get_node_info(self, run_id: str, node_id: str) -> Optional[WorkNodeStatus]: ...

    @abstractmethod
    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRunInfoResponse]: ...

    @abstractmethod
    async def get_input_node(
        self, run_id: str, node_id: str
    ) -> Optional[InputNodeResponse]: ...

    @abstractmethod
    async def patch_node_data(
        self,
        run_id: str,
        node_id: str,
        payload: ArtifactPayload,
    ) -> Optional[NodePatchResponse]: ...

    @abstractmethod
    async def submit_node_data(
        self,
        run_id: str,
        node_id: str,
    ) -> Optional[NodePatchResponse]: ...

    @abstractmethod
    async def get_node_output_data(
    self,
    run_id: str,
    node_id: str,
) -> Optional[TaskNodeOutputResponse]: ...
