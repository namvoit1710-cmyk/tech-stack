from enum import Enum


class WorkflowStatus(str, Enum):
	PENDING = "PENDING"
	COMPLETED = "COMPLETED"
	FAILED = "FAILED"
