from enum import Enum


class DomainState(str, Enum):
    CLASSIFYING = "classifying"
    COLLECTING_INFO = "collecting_info"
    ROUTING = "routing"
    EXECUTING = "executing"
    FORMATTING = "formatting"
    COMPLETED = "completed"
    ERROR = "error"
