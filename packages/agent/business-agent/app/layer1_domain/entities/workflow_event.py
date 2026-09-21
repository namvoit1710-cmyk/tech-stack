from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class PendingKind(str, Enum):
    INPUT = "input"
    FILE_UPLOAD = "file_upload"
    DATA_EDIT = "data_edit"
    HUMAN_ACTION = "human_action"


class WorkflowStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"


@dataclass
class WorkflowRun:
    status: str
    correlation_id: str = ""
    tenant_id: str = ""
    parent_run_id: str = ""
    depth_level: int = 0
    started_at: str = ""
    is_test: bool = False


@dataclass
class WorkflowInfo:
    name: str
    description: str = ""
    version: str = ""


@dataclass
class NodeInfo:
    name: str
    node_type: str
    description: str = ""
    worker_type: str = ""

@dataclass
class HttpRequestInput:
    method: str
    url: str
    timeout_seconds: int
    follow_redirects: bool
    verify_ssl: bool
    _instruction: str

@dataclass
class WorkflowEvent:
    event_id: str
    timestamp: str
    source: str
    root_run_id: str
    depth: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    task_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    node_name: str = ""
    instruction: str = ""
    pending_kind: PendingKind = PendingKind.INPUT

    status: WorkflowStatus = WorkflowStatus.STARTED
    execution_time_ms: int = 0
    iteration_index: Optional[int] = None
    branch: str = ""
    expression: str = ""
    condition_result: Optional[bool] = None

    event_type: str = ""
    run: Optional[WorkflowRun] = None
    workflow: Optional[WorkflowInfo] = None
    node: Optional[NodeInfo] = None
    confirm_label: Optional[str] = None
    reject_label: Optional[str] = None
    output_port: Optional[str] = None
    input: Optional[HttpRequestInput] = None
    change_request_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowEvent":
        return cls(
            event_id=data.get("event_id", ""),
            timestamp=data.get("timestamp", ""),
            source=data.get("source", ""),
            root_run_id=data.get("root_run_id", ""),
            depth=data.get("depth", 0),
            metadata=data.get("metadata", {}),
            task_id=data.get("task_id", ""),
            run_id=data.get("run_id", ""),
            workflow_id=data.get("workflow_id", ""),
            node_id=data.get("node_id", ""),
            node_name=data.get("node_name", ""),
            instruction=data.get("instruction", ""),
            confirm_label=data.get("confirm_label"),
            reject_label=data.get("reject_label"),
            pending_kind=data.get("pending_kind", ""),
            status=data.get("status", ""),
            execution_time_ms=data.get("execution_time_ms", 0),
            iteration_index=data.get("iteration_index"),
            branch=data.get("branch", ""),
            expression=data.get("expression", ""),
            condition_result=data.get("condition_result"),
            event_type=data.get("_event", ""),
            run=WorkflowRun(**data["_run"]) if data.get("_run") else None,
            input=HttpRequestInput(**data["input"]) if data.get("input") else None,
            workflow=WorkflowInfo(**data["_workflow"])
            if data.get("_workflow")
            else None,
            node=NodeInfo(**data["_node"]) if data.get("_node") else None,
            change_request_id=data.get("change_request_id"),
        )
