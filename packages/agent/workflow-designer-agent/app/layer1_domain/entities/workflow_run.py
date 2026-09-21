from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkflowRef:
    """A persisted workflow: its id and a link to view it."""

    workflow_id: str
    link: str = ""


@dataclass
class RunOutcome:
    """The result of test-running a workflow."""

    status: str = ""          # control-plane run status (e.g. COMPLETED, FAILED, RUNNING)
    run_id: str = ""
    error: str = ""
    error_code: str = ""
    details: dict = field(default_factory=dict)
    replay_link: str = ""

    def failed(self) -> bool:
        return bool(self.error) or self.status.upper() in {"FAILED", "CANCELLED", "ERROR"}


@dataclass
class DryRunOutcome:
    """The result of side-effect-free dry-running a persisted workflow."""

    ok: bool = False
    error_code: str = ""
    error_message: str = ""
    error_details: dict = field(default_factory=dict)
