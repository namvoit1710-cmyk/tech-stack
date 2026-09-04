import asyncio

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)
from smart_service_sdk.layer2_application.features.background_job.background_job_coordinator import (
    BackgroundJobCoordinator,
)


class _Logger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args, **kwargs) -> None:
        self.info_calls.append((args, kwargs))

    def exception(self, *args, **kwargs) -> None:
        del args, kwargs


class _BackgroundJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], BackgroundJobRecord] = {}

    async def save(self, job: BackgroundJobRecord) -> BackgroundJobRecord:
        assert job.id is not None
        self.jobs[(job.tenant_id, job.id)] = BackgroundJobRecord(
            id=job.id,
            tenant_id=job.tenant_id,
            job_type=job.job_type,
            status=job.status,
            progress=job.progress,
            detail=dict(job.detail),
            error_message=job.error_message,
            started_at=job.started_at,
            ended_at=job.ended_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        return self.jobs[(job.tenant_id, job.id)]

    async def get(self, tenant_id: str, job_id: str) -> BackgroundJobRecord | None:
        return self.jobs.get((tenant_id, job_id))

    async def list_by_status(self, status: str) -> list[BackgroundJobRecord]:
        return [job for job in self.jobs.values() if job.status == status]


class _Importer:
    def __init__(self, *, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[tuple[str, str]] = []

    async def run_import_job_for_file(self, *, file_id: str, tenant_id: str):
        self.calls.append((tenant_id, file_id))
        if file_id in self.failures:
            raise RuntimeError(f"failed for {file_id}")

        class _Result:
            def __init__(self) -> None:
                self.row_count = 2
                self.metadata = {"file_name": f"{file_id}.csv", "row_count": 2}

        return _Result()


async def _wait_for_tasks(
    coordinator: BackgroundJobCoordinator,
) -> None:
    if coordinator._tasks:
        await asyncio.wait(coordinator._tasks.values())


def test_create_import_job_deduplicates_and_completes() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer()
    coordinator = BackgroundJobCoordinator(
        logger=_Logger(),
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-default",
    )

    async def scenario() -> None:
        accepted = await coordinator.create_import_job(
            file_ids=[" file-a ", "", "file-a", "file-b"],
            tenant_id=None,
        )
        assert accepted.status == "accepted"
        assert accepted.file_ids == ["file-a", "file-b"]
        await _wait_for_tasks(coordinator)
        completed = await coordinator.get_background_job(
            job_id=accepted.job_id,
            tenant_id="tenant-default",
        )
        assert completed.status == "completed"
        assert [item.file_id for item in completed.file_results] == ["file-a", "file-b"]
        assert [item.row_count for item in completed.file_results] == [2, 2]

    asyncio.run(scenario())

    assert importer.calls == [
        ("tenant-default", "file-a"),
        ("tenant-default", "file-b"),
    ]


def test_create_import_job_marks_partial_failure() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer(failures={"file-b"})
    coordinator = BackgroundJobCoordinator(
        logger=_Logger(),
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-1",
    )

    async def scenario() -> None:
        accepted = await coordinator.create_import_job(
            file_ids=["file-a", "file-b"],
            tenant_id="tenant-1",
        )
        await _wait_for_tasks(coordinator)
        completed = await coordinator.get_background_job(
            job_id=accepted.job_id,
            tenant_id="tenant-1",
        )
        assert completed.status == "partial_failed"
        assert completed.error_message == "failed for file-b"
        assert completed.file_results[0].status == "completed"
        assert completed.file_results[1].status == "failed"
        assert completed.file_results[1].error_message == "failed for file-b"

    asyncio.run(scenario())


def test_resume_running_jobs_restarts_only_pending_files() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer()
    logger = _Logger()
    running_job = BackgroundJobRecord(
        id="job-1",
        tenant_id="tenant-1",
        job_type="duplicate_index_import",
        status="running",
        detail={
            "file_ids": ["file-a", "file-b", "file-c"],
            "file_results": [
                {"file_id": "file-a", "status": "completed", "row_count": 2, "metadata": {}},
                {"file_id": "file-b", "status": "running", "row_count": 0, "metadata": {}},
                {"file_id": "file-c", "status": "accepted", "row_count": 0, "metadata": {}},
            ],
        },
    )
    asyncio.run(repository.save(running_job))
    coordinator = BackgroundJobCoordinator(
        logger=logger,
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-1",
    )

    async def scenario() -> None:
        await coordinator.resume_running_jobs()
        await _wait_for_tasks(coordinator)
        completed = await coordinator.get_background_job(
            job_id="job-1",
            tenant_id="tenant-1",
        )
        assert completed.status == "completed"
        assert [item.file_id for item in completed.file_results] == [
            "file-a",
            "file-b",
            "file-c",
        ]
        assert [item.status for item in completed.file_results] == [
            "completed",
            "completed",
            "completed",
        ]

    asyncio.run(scenario())

    assert importer.calls == [
        ("tenant-1", "file-b"),
        ("tenant-1", "file-c"),
    ]
    assert logger.info_calls[0][0][0] == "Restarting background job on startup"
    assert logger.info_calls[0][1]["job_id"] == "job-1"
    assert logger.info_calls[0][1]["file_ids"] == ["file-b", "file-c"]


def test_resume_running_jobs_logs_when_nothing_is_running() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer()
    logger = _Logger()
    coordinator = BackgroundJobCoordinator(
        logger=logger,
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-1",
    )

    async def scenario() -> None:
        await coordinator.resume_running_jobs()

    asyncio.run(scenario())

    assert importer.calls == []
    assert logger.info_calls == [
        (("No background jobs to resume on startup",), {})
    ]


def test_resume_running_jobs_skips_jobs_already_scheduled() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer()
    running_job = BackgroundJobRecord(
        id="job-1",
        tenant_id="tenant-1",
        job_type="duplicate_index_import",
        status="running",
        detail={"file_ids": ["file-a"], "file_results": [{"file_id": "file-a", "status": "accepted"}]},
    )
    asyncio.run(repository.save(running_job))
    coordinator = BackgroundJobCoordinator(
        logger=_Logger(),
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-1",
    )
    coordinator._tasks["job-1"] = object()  # type: ignore[assignment]

    async def scenario() -> None:
        await coordinator.resume_running_jobs()

    asyncio.run(scenario())

    assert importer.calls == []


def test_resume_running_jobs_finalizes_stale_running_job_without_rerun() -> None:
    repository = _BackgroundJobRepository()
    importer = _Importer()
    stale_job = BackgroundJobRecord(
        id="job-1",
        tenant_id="tenant-1",
        job_type="duplicate_index_import",
        status="running",
        detail={
            "file_ids": ["file-a", "file-b"],
            "file_results": [
                {"file_id": "file-a", "status": "completed", "row_count": 2, "metadata": {}},
                {
                    "file_id": "file-b",
                    "status": "failed",
                    "row_count": 0,
                    "error_message": "failed for file-b",
                    "metadata": {},
                },
            ],
        },
    )
    asyncio.run(repository.save(stale_job))
    coordinator = BackgroundJobCoordinator(
        logger=_Logger(),
        background_job_repository=repository,
        job_runner=importer,
        default_tenant_id="tenant-1",
    )

    async def scenario() -> None:
        await coordinator.resume_running_jobs()
        finalized = await coordinator.get_background_job(
            job_id="job-1",
            tenant_id="tenant-1",
        )
        assert finalized.status == "partial_failed"
        assert finalized.error_message == "failed for file-b"

    asyncio.run(scenario())

    assert importer.calls == []
