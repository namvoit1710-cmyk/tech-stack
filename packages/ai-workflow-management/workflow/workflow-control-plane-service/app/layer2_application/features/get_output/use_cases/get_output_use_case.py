"""Get output data for a run."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.run_repository_interface import IRunRepository


@dataclass
class GetOutputCommand:
    run_id: str = ""



@dataclass
class GetOutputResult:
    run_id: str = ""
    output_data: dict[str, Any] | None = None
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)



class GetOutputUseCase:
    def __init__(self, logger: ILogger, run_repo: IRunRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.run_repo = run_repo

    async def execute(self, command: GetOutputCommand) -> GetOutputResult:
        self.logger.info("Getting output", run_id=command.run_id)
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return GetOutputResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )
        return GetOutputResult(run_id=command.run_id, output_data=run.output_data)


