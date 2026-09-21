from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient


@dataclass
class CollectInputDto:
    run_id: str = ""
    workflow_id: str = ""    # resolve the run from a workflow when run_id is unknown
    workflow_name: str = ""


@dataclass
class CollectOutputDto:
    run_id: str
    execution_log: dict[str, Any] = field(default_factory=dict)


class CollectUseCase:
    """Step 1 — read the run's execution log from the Workflow Control Plane (read-only).

    Accepts a `run_id` directly, or resolves one from `workflow_id` / `workflow_name`
    (latest failed run) when the caller only knows the workflow."""

    def __init__(self, logger: ILogger, workflow_client: IWorkflowClient):
        self._logger = logger
        self._workflow_client = workflow_client

    async def execute(self, dto: CollectInputDto) -> CollectOutputDto:
        run_id = dto.run_id
        if not run_id and (dto.workflow_id or dto.workflow_name):
            self._logger.info(
                f"Resolving run from workflow_id={dto.workflow_id!r} name={dto.workflow_name!r}"
            )
            run_id = await self._workflow_client.resolve_run_id(
                workflow_id=dto.workflow_id, workflow_name=dto.workflow_name
            )
        if not run_id:
            self._logger.warning("No run_id could be determined; nothing to collect")
            return CollectOutputDto(run_id="", execution_log={})

        self._logger.info(f"Collecting execution-log for run {run_id}")
        execution_log = await self._workflow_client.get_execution_log(run_id)
        return CollectOutputDto(run_id=run_id, execution_log=execution_log)
