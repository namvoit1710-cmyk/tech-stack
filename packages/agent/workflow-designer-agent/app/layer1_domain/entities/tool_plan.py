from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolPlan:
    """The single tool the planner chose to run, with its input."""

    tool: str
    tool_input: dict = field(default_factory=dict)
