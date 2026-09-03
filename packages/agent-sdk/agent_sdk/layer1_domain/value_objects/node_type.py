from enum import Enum


class NodeType(Enum):
    INPUT_GUARD = "input_guard"
    OUTPUT_GUARD = "output_guard"
    PERMISSION_CHECK = "permission_check"
    CONVERSATION_MANAGER = "conversation_manager"
    FORMAT_RESPONSE = "format_response"
    PUBLISH_EVENTS = "publish_events"
    ERROR_HANDLER = "error_handler"
    CUSTOM = "custom"
