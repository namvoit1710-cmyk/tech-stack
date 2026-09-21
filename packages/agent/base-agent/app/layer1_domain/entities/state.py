from app.layer1_domain.entities.queue import QueueDefinition
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.workflow_execution_result import WorkflowExecutionResult
from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

# TODO: if needed new fields
class MainState(AgentBaseState):
    workflow: WorkflowDefinition
    queue: QueueDefinition
    plan: ExecutionPlan
    execution_result: WorkflowExecutionResult
