import asyncio

from app.layer4_frameworks.clients.in_process_import_job_submitter import (
    InProcessImportJobSubmitter,
)


class _SdkResultFile:
    file_id = "file-z"
    status = "accepted"
    row_count = 0
    started_at = None
    ended_at = None
    error_message = ""
    metadata = {}


class _CreateImportJobUseCase:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def execute(self, command):
        self.commands.append(command)
        return type(
            "_SdkResult",
            (),
            {
                "job_id": "job-789",
                "tenant_id": "tenant-9",
                "status": "accepted",
                "file_ids": ["file-z"],
                "accepted_at": "2026-06-19T01:02:03+00:00",
                "started_at": None,
                "ended_at": None,
                "error_message": "",
                "file_results": [_SdkResultFile()],
            },
        )()


def test_in_process_import_job_submitter_maps_sdk_response() -> None:
    create_usecase = _CreateImportJobUseCase()
    submitter = InProcessImportJobSubmitter(create_usecase)

    result = asyncio.run(
        submitter.submit_import_job(
            type(
                "_Request",
                (),
                {"file_ids": ["file-z"], "tenant_id": "tenant-9"},
            )()
        )
    )

    command = create_usecase.commands[0]
    assert command.file_ids == ["file-z"]
    assert command.tenant_id == "tenant-9"
    assert result.job_id == "job-789"
    assert result.file_ids == ["file-z"]
    assert result.file_results == [
        {
            "file_id": "file-z",
            "status": "accepted",
            "row_count": 0,
            "started_at": None,
            "ended_at": None,
            "error_message": "",
            "metadata": {},
        }
    ]
