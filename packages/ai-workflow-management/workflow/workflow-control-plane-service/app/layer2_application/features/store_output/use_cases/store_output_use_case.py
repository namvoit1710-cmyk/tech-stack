"""Store output data for a run."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class StoreOutputCommand:
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    reference: str = ""



@dataclass
class StoreOutputResult:
    run_id: str = ""
    reference: str = ""
    stored: bool = False



class StoreOutputUseCase:
    def __init__(self, logger: ILogger, **kwargs: Any) -> None:
        self.logger = logger

    async def execute(self, command: StoreOutputCommand) -> StoreOutputResult:
        self.logger.info("Storing output", run_id=command.run_id)
        return StoreOutputResult(
            run_id=command.run_id,
            reference=f"s3://outputs/{command.run_id}",
            stored=True,
        )


