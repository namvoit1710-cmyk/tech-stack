"""Get approval process for a change request."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.governance_client_interface import IGovernanceClient
from app.layer2_application.read.approval_process_transformer import (
    ApprovalProcessTransformer,
)


@dataclass
class GetApprovalProcessCommand:
    """Command to get approval process for a change request."""

    cr_id: str = ""


@dataclass
class GetApprovalProcessResult:
    """Result containing the approval process graph."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""


class GetApprovalProcessUseCase:
    """Fetch and transform approval process data from governance API."""

    def __init__(
        self,
        governance_client: IGovernanceClient,
        transformer: ApprovalProcessTransformer | None = None,
    ) -> None:
        self._governance_client = governance_client
        self._transformer = transformer or ApprovalProcessTransformer()

    async def execute(self, command: GetApprovalProcessCommand) -> GetApprovalProcessResult:
        """Fetch approval process and transform to graph structure.

        Args:
            command: Command containing the change request ID.

        Returns:
            GetApprovalProcessResult with nodes and edges, or error details.
        """
        if not command.cr_id:
            return GetApprovalProcessResult(
                error="Change request ID is required",
                error_code="INVALID_INPUT",
            )

        approval_data = await self._governance_client.get_approval_process(command.cr_id)
        approval_steps = approval_data.get("processes")
        if isinstance(approval_steps, list):
            approval_steps.sort(key=lambda x: (x.get("stepOrder"), x.get("stepId")))

        if approval_steps:
            graph = self._transformer.transform(approval_steps)
            graph_dict = graph.to_dict()
            return GetApprovalProcessResult(
                nodes=graph_dict["nodes"],
                edges=graph_dict["edges"],
            )

        return GetApprovalProcessResult(
            nodes=[],
            edges=[],
        )
