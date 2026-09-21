from enum import Enum
from dataclasses import dataclass


class ReviewContextType(str, Enum):
    LLM = "llm"

class ReviewStatus(str, Enum):
    FAIL = "fail"
    SUCCESS = "success"
    NEED_USER_INPUT = "need_user_input"

@dataclass
class ReviewContext():
    node_information: str
    expected_output: str
    actual_output: str
