from enum import Enum

from app.layer1_domain.value_objects.node_class import NodeClass  # noqa: F401 — re-export for convenience


class NodeType(str, Enum):
    TASK = "TASK"
    PARALLEL = "PARALLEL"
    CONDITION = "CONDITION"
    LOOP = "LOOP"
    SWITCH = "SWITCH"
    MERGE = "MERGE"
    TRIGGER = "TRIGGER"
    WORKFLOW = "WORKFLOW"
    COMPUTE = "COMPUTE"
    INPUT = "INPUT"
    HUMAN_ACTION = "HUMAN_ACTION"
    DATA_EDIT = "DATA_EDIT"
    TEMPLATE_DATA_EDIT = "TEMPLATE_DATA_EDIT"
    FILE_UPLOAD = "FILE_UPLOAD"
    LOOP_EXIT = "LOOP_EXIT"
    LOOP_CONTINUE = "LOOP_CONTINUE"
    OUTPUT = "OUTPUT"
    APPROVAL_FLOW = "APPROVAL_FLOW"
    SET_VARIABLE = "SET_VARIABLE"
    PROMPT = "PROMPT"
    WAIT = "WAIT"
