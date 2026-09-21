from enum import Enum


class GraphNode(Enum):
    """Graph node identifiers for CSV Agent workflow."""

    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
