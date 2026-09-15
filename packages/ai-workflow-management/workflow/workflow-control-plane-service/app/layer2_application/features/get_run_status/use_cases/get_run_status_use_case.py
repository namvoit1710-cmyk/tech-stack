"""Get the status of a workflow run."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.errors import ErrorCode


@dataclass
class GetRunStatusCommand:
    run_id: str = ""



@dataclass
class GetRunStatusResult:
    id: str = ""
    workflow_id: str = ""
    status: str = ""
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] | None = None



class GetRunStatusUseCase:
    """Phase 4a: reads via ``IRunContextViewStore``.

    Previously depended on ``IRunRepository`` directly; now depends on the
    read-side view store so the Phase 4b Projector swap requires no change
    here.
    """

    def __init__(
        self,
        logger: ILogger,
        run_context_view_store: IRunContextViewStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_context_view_store = run_context_view_store

    async def execute(self, command: GetRunStatusCommand) -> GetRunStatusResult:
        view = self.run_context_view_store.get(command.run_id)
        if not view:
            return GetRunStatusResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )
        return GetRunStatusResult(
            id=view.run_id,
            workflow_id=view.workflow_id,
            status=view.status,
            started_at=view.started_at,
            completed_at=view.completed_at,
            error=view.error,
            output_data=view.output_data,
        )
