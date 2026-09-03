from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepConfig:
    id: str = ""
    type: str = ""
    next_step: str = ""
    true_branch: str = ""
    false_branch: str = ""
    routes: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    subflow_ref: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "next_step": self.next_step,
            "true_branch": self.true_branch,
            "false_branch": self.false_branch,
            "routes": self.routes,
            "params": self.params,
            "subflow_ref": self.subflow_ref,
        }


@dataclass
class FlowConfig:
    agent_type: str = ""
    flow_type: str = ""
    version: int = 1
    description: str = ""
    steps: list[StepConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._step_index: dict[str, StepConfig] = {step.id: step for step in self.steps}

    def step_count(self) -> int:
        return len(self.steps)

    def get_step(self, step_id: str) -> Optional[StepConfig]:
        return self._step_index.get(step_id)
