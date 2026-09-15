"""Builtin node specs — importing this package registers every spec.

``TYPED_CONFIGS`` maps each node type to its handler-facing config
dataclass; ``tests/unit/node_spec/test_spec_alignment.py`` asserts the
dataclass fields stay aligned with the spec's config keys.
"""

from app.layer1_domain.node_spec.specs.approval_flow import (
    APPROVAL_FLOW_SPEC,
    ApprovalFlowConfig,
)
from app.layer1_domain.node_spec.specs.compute import COMPUTE_SPEC, ComputeConfig
from app.layer1_domain.node_spec.specs.condition import (
    CONDITION_SPEC,
    ConditionNodeConfig,
)
from app.layer1_domain.node_spec.specs.data_edit import DATA_EDIT_SPEC, DataEditConfig
from app.layer1_domain.node_spec.specs.file_upload import (
    FILE_UPLOAD_SPEC,
    FileUploadConfig,
)
from app.layer1_domain.node_spec.specs.human_action import (
    HUMAN_ACTION_SPEC,
    HumanActionConfig,
)
from app.layer1_domain.node_spec.specs.input import INPUT_SPEC, InputNodeConfig
from app.layer1_domain.node_spec.specs.loop import LOOP_SPEC, LoopConfig
from app.layer1_domain.node_spec.specs.loop_exit import (
    LOOP_CONTINUE_SPEC,
    LOOP_EXIT_SPEC,
)
from app.layer1_domain.node_spec.specs.merge import MERGE_SPEC, MergeConfig
from app.layer1_domain.node_spec.specs.output import OUTPUT_SPEC, OutputNodeConfig
from app.layer1_domain.node_spec.specs.parallel import PARALLEL_SPEC, ParallelConfig
from app.layer1_domain.node_spec.specs.prompt import PROMPT_SPEC, PromptConfig
from app.layer1_domain.node_spec.specs.set_variable import (
    SET_VARIABLE_SPEC,
    SetVariableConfig,
)
from app.layer1_domain.node_spec.specs.switch import SWITCH_SPEC, SwitchNodeConfig
from app.layer1_domain.node_spec.specs.task import TASK_SPEC, TaskConfig
from app.layer1_domain.node_spec.specs.template_data_edit import (
    TEMPLATE_DATA_EDIT_SPEC,
    TemplateDataEditConfig,
)
from app.layer1_domain.node_spec.specs.trigger import TRIGGER_SPEC, TriggerConfig
from app.layer1_domain.node_spec.specs.wait import WAIT_SPEC, WaitConfig
from app.layer1_domain.node_spec.specs.workflow_node import (
    WORKFLOW_SPEC,
    WorkflowNodeConfig,
)
from app.layer1_domain.value_objects.node_type import NodeType

TYPED_CONFIGS: dict[NodeType, type] = {
    NodeType.APPROVAL_FLOW: ApprovalFlowConfig,
    NodeType.COMPUTE: ComputeConfig,
    NodeType.CONDITION: ConditionNodeConfig,
    NodeType.DATA_EDIT: DataEditConfig,
    NodeType.FILE_UPLOAD: FileUploadConfig,
    NodeType.HUMAN_ACTION: HumanActionConfig,
    NodeType.INPUT: InputNodeConfig,
    NodeType.LOOP: LoopConfig,
    NodeType.MERGE: MergeConfig,
    NodeType.OUTPUT: OutputNodeConfig,
    NodeType.PARALLEL: ParallelConfig,
    NodeType.PROMPT: PromptConfig,
    NodeType.SET_VARIABLE: SetVariableConfig,
    NodeType.SWITCH: SwitchNodeConfig,
    NodeType.TASK: TaskConfig,
    NodeType.TEMPLATE_DATA_EDIT: TemplateDataEditConfig,
    NodeType.TRIGGER: TriggerConfig,
    NodeType.WAIT: WaitConfig,
    NodeType.WORKFLOW: WorkflowNodeConfig,
}

__all__ = [
    "APPROVAL_FLOW_SPEC", "ApprovalFlowConfig",
    "COMPUTE_SPEC", "ComputeConfig",
    "CONDITION_SPEC", "ConditionNodeConfig",
    "DATA_EDIT_SPEC", "DataEditConfig",
    "FILE_UPLOAD_SPEC", "FileUploadConfig",
    "HUMAN_ACTION_SPEC", "HumanActionConfig",
    "INPUT_SPEC", "InputNodeConfig",
    "LOOP_SPEC", "LoopConfig",
    "LOOP_EXIT_SPEC", "LOOP_CONTINUE_SPEC",
    "MERGE_SPEC", "MergeConfig",
    "OUTPUT_SPEC", "OutputNodeConfig",
    "PARALLEL_SPEC", "ParallelConfig",
    "PROMPT_SPEC", "PromptConfig",
    "SET_VARIABLE_SPEC", "SetVariableConfig",
    "SWITCH_SPEC", "SwitchNodeConfig",
    "TASK_SPEC", "TaskConfig",
    "TEMPLATE_DATA_EDIT_SPEC", "TemplateDataEditConfig",
    "TRIGGER_SPEC", "TriggerConfig",
    "WAIT_SPEC", "WaitConfig",
    "WORKFLOW_SPEC", "WorkflowNodeConfig",
    "TYPED_CONFIGS",
]
