from enum import Enum


class AssignType(str, Enum):
    USER = "USER"
    GROUP = "GROUP"
    SYSTEM = "SYSTEM"
    ROLE = "ROLE"

class ApprovalProcessAction(str, Enum):
    EDIT = "EDIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REWORK = "REWORK"
    ACTIVE = "ACTIVE"
    TROUBLESHOOT = "TROUBLESHOOT"

class ProcessExecutionMode(str, Enum):
    SINGLE_TASK = "SINGLE_TASK"
    MULTI_TASK = "MULTI_TASK"


class ContributeResolutionStrategy(str, Enum):
    ALL_CONTRIBUTED = "ALL_CONTRIBUTED"
    ANY_CONTRIBUTED = "ANY_CONTRIBUTED"


class ApprovalResolutionStrategy(str, Enum):
    ALL_APPROVED = "ALL_APPROVED"
    ANY_APPROVED = "ANY_APPROVED"
    ALL_REJECTED = "ALL_REJECTED"
    ANY_REJECTED = "ANY_REJECTED"
    MORE_THAN_HALF_APPROVED = "MORE_THAN_HALF_APPROVED"


class Validity(Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    WARNING = "warning"
