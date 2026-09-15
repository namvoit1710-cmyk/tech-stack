"""Use case for triggering workflow as internal function."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunUseCase,
    StartRunCommand,
)
from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient


@dataclass
class InternalTriggerWorkflowResult:
    run_id: str = ""
    workflow_id: str = ""
    status: str = ""
    file_id: str | None = None
    record_count: int = 0
    error: str | None = None


class InternalTriggerWorkflowUseCase:
    """Execute a workflow and optionally return result as file_id.
    
    Two modes:
    1. wait_for_completion=False: Returns run_id immediately.
       Frontend subscribes to socket and waits for run.completed event.
       
    2. wait_for_completion=True: Polls for completion (not recommended for long workflows)
       Returns file_id with workflow output.
    """
    
    def __init__(
        self,
        logger: ILogger,
        start_run_usecase: StartRunUseCase,
        file_service_client: IFileServiceClient,
    ) -> None:
        self._logger = logger
        self._start_run = start_run_usecase
        self._file_service = file_service_client
    
    async def execute(
        self,
        workflow_id: str,
        input_data: dict[str, Any] | None = None,
        wait_for_completion: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Execute workflow and return result."""
        
        # Start the workflow run
        command = StartRunCommand(
            workflow_id=workflow_id,
            input_data=input_data or {},
            skip_validation=True,
            execution_mode="auto",
        )
        
        result = await self._start_run.execute(command)
        
        if result.error:
            return {
                "error": result.error,
                "error_code": result.error_code,
            }
        
        if not wait_for_completion:
            # Return immediately - frontend will wait for socket event
            return {
                "run_id": result.id,
                "workflow_id": workflow_id,
                "status": result.status,
                "message": "Workflow started. Subscribe to socket room for completion event.",
            }
        
        # Note: Polling for completion is not recommended for production
        # Frontend should use socket events instead
        import asyncio
        
        max_attempts = timeout_seconds // 2
        for _ in range(max_attempts):
            await asyncio.sleep(2)
            
            # TODO: Check run status via repository
            # For now, just return the initial result
            # Real implementation would poll run_repo.get_by_id(result.id)
            
        return {
            "run_id": result.id,
            "workflow_id": workflow_id,
            "status": "TIMEOUT",
            "error": f"Workflow did not complete within {timeout_seconds} seconds",
        }
