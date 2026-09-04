from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)


@dataclass(frozen=True)
class BackgroundJobFileResult:
    file_id: str
    status: str
    row_count: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BackgroundJobResult:
    job_id: str
    tenant_id: str
    status: str
    file_ids: list[str]
    accepted_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str = ""
    file_results: list[BackgroundJobFileResult] = field(default_factory=list)


class BackgroundJobCoordinator:
    def __init__(
        self,
        *,
        logger,
        background_job_repository,
        job_runner,
        default_tenant_id: str,
    ) -> None:
        self.logger = logger
        self.background_job_repository = background_job_repository
        self.job_runner = job_runner
        self.default_tenant_id = default_tenant_id
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def resume_running_jobs(self) -> None:
        running_jobs = await self.background_job_repository.list_by_status("running")
        if not running_jobs:
            self.logger.info("No background jobs to resume on startup")
            return

        resumed_job_count = 0
        finalized_job_count = 0
        skipped_job_count = 0
        for job in running_jobs:
            job_id = str(job.id or "").strip()
            if not job_id or job_id in self._tasks:
                skipped_job_count += 1
                continue

            resumable_file_ids = self._resumable_file_ids(job)
            if resumable_file_ids:
                resumed_job_count += 1
                self.logger.info(
                    "Restarting background job on startup",
                    tenant_id=job.tenant_id or self.default_tenant_id,
                    job_id=job_id,
                    file_ids=resumable_file_ids,
                    file_count=len(resumable_file_ids),
                )
                self._schedule_import_job(
                    tenant_id=job.tenant_id or self.default_tenant_id,
                    job_id=job_id,
                    file_ids=resumable_file_ids,
                )
                continue

            finalized_job_count += 1
            await self._finalize_job(job)
        if resumed_job_count == 0:
            self.logger.info(
                "No background jobs resumed on startup",
                running_job_count=len(running_jobs),
                finalized_job_count=finalized_job_count,
                skipped_job_count=skipped_job_count,
            )
            return
        self.logger.info(
            "Background job restart scheduled on startup",
            running_job_count=len(running_jobs),
            resumed_job_count=resumed_job_count,
            finalized_job_count=finalized_job_count,
            skipped_job_count=skipped_job_count,
        )

    async def create_import_job(
        self,
        *,
        file_ids: list[str],
        tenant_id: str | None,
    ) -> BackgroundJobResult:
        resolved_tenant_id = (tenant_id or self.default_tenant_id).strip()
        normalized_file_ids = list(
            dict.fromkeys(str(file_id).strip() for file_id in file_ids if str(file_id).strip())
        )
        if not normalized_file_ids:
            raise ValueError("file_ids must contain at least one non-empty file ID")

        accepted_at = self._utc_now_iso()
        file_results = [
            {
                "file_id": file_id,
                "status": "accepted",
                "row_count": 0,
                "started_at": None,
                "ended_at": None,
                "error_message": "",
                "metadata": {},
            }
            for file_id in normalized_file_ids
        ]
        job = BackgroundJobRecord(
            id=str(uuid4()),
            tenant_id=resolved_tenant_id,
            job_type="duplicate_index_import",
            status="accepted",
            detail={
                "accepted_at": accepted_at,
                "file_ids": normalized_file_ids,
                "file_results": file_results,
            },
        )
        await self.background_job_repository.save(job)
        self._schedule_import_job(
            tenant_id=resolved_tenant_id,
            job_id=str(job.id),
            file_ids=normalized_file_ids,
        )
        return self._job_result_from_record(job)

    async def get_background_job(
        self,
        *,
        job_id: str,
        tenant_id: str | None,
    ) -> BackgroundJobResult:
        resolved_tenant_id = (tenant_id or self.default_tenant_id).strip()
        job = await self.background_job_repository.get(
            resolved_tenant_id,
            job_id,
        )
        if job is None:
            raise LookupError(f"Background job not found: {job_id}")
        return self._job_result_from_record(job)

    async def _run_import_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_ids: list[str],
    ) -> None:
        job = await self.background_job_repository.get(tenant_id, job_id)
        if job is None:
            return
        metadata = job.detail
        metadata.setdefault("file_ids", list(file_ids))
        metadata.setdefault("file_results", [])
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.ended_at = None
        job.error_message = ""
        await self._save_job(tenant_id, job)

        for index, file_id in enumerate(file_ids):
            file_result = self._find_or_create_file_result(metadata, file_id)
            file_result["status"] = "running"
            file_result["started_at"] = self._utc_now_iso()
            file_result["ended_at"] = None
            file_result["error_message"] = ""
            await self._save_job(tenant_id, job)
            try:
                job_result = await self.job_runner.run_import_job_for_file(
                    file_id=file_id,
                    tenant_id=tenant_id,
                )
                file_result["status"] = "completed"
                file_result["row_count"] = job_result.row_count
                file_result["metadata"] = dict(job_result.metadata)
                self.logger.info(
                    "Duplicate import file completed",
                    tenant_id=tenant_id,
                    job_id=job_id,
                    file_id=file_id,
                    row_count=job_result.row_count,
                    file_index=index,
                )
            except Exception as exc:
                file_result["status"] = "failed"
                file_result["error_message"] = str(exc)
                file_result["metadata"] = {}
                self.logger.exception(
                    "Duplicate import file failed",
                    tenant_id=tenant_id,
                    job_id=job_id,
                    file_id=file_id,
                    error=str(exc),
                )
            finally:
                file_result["ended_at"] = self._utc_now_iso()
                await self._save_job(tenant_id, job)

        await self._finalize_job(job)

    async def _save_job(self, tenant_id: str, job: BackgroundJobRecord) -> None:
        if job.tenant_id != tenant_id:
            job.tenant_id = tenant_id
        await self.background_job_repository.save(job)

    def _schedule_import_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_ids: list[str],
    ) -> None:
        task = asyncio.create_task(
            self._run_import_job(
                tenant_id=tenant_id,
                job_id=job_id,
                file_ids=file_ids,
            )
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    @staticmethod
    def _find_or_create_file_result(
        metadata: dict[str, object],
        file_id: str,
    ) -> dict[str, object]:
        file_results = metadata.setdefault("file_results", [])
        if not isinstance(file_results, list):
            file_results = []
            metadata["file_results"] = file_results
        for item in file_results:
            if isinstance(item, dict) and str(item.get("file_id", "")) == file_id:
                return item
        created = {
            "file_id": file_id,
            "status": "accepted",
            "row_count": 0,
            "started_at": None,
            "ended_at": None,
            "error_message": "",
            "metadata": {},
        }
        file_results.append(created)
        return created

    @staticmethod
    def _resumable_file_ids(job: BackgroundJobRecord) -> list[str]:
        metadata = job.detail if isinstance(job.detail, dict) else {}
        file_ids = [
            str(file_id).strip()
            for file_id in metadata.get("file_ids", [])
            if str(file_id).strip()
        ]
        if not file_ids:
            return []

        file_results_by_id: dict[str, dict[str, object]] = {}
        raw_file_results = metadata.get("file_results", [])
        if isinstance(raw_file_results, list):
            for item in raw_file_results:
                if not isinstance(item, dict):
                    continue
                file_id = str(item.get("file_id", "")).strip()
                if file_id:
                    file_results_by_id[file_id] = item

        resumable_file_ids: list[str] = []
        for file_id in file_ids:
            file_result = file_results_by_id.get(file_id)
            status = str(file_result.get("status", "accepted")) if file_result else "accepted"
            if status in {"accepted", "running"}:
                resumable_file_ids.append(file_id)
        return resumable_file_ids

    async def _finalize_job(self, job: BackgroundJobRecord) -> None:
        metadata = job.detail if isinstance(job.detail, dict) else {}
        file_results = metadata.get("file_results", [])
        success_count = 0
        failure_count = 0
        running_count = 0
        accepted_count = 0
        last_error_message = ""

        if isinstance(file_results, list):
            for item in file_results:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "accepted")).strip().lower()
                if status == "completed":
                    success_count += 1
                elif status == "failed":
                    failure_count += 1
                    if not last_error_message:
                        last_error_message = str(item.get("error_message", "")).strip()
                elif status == "running":
                    running_count += 1
                elif status == "accepted":
                    accepted_count += 1

        if running_count > 0 or accepted_count > 0:
            job.status = "running"
            job.error_message = ""
            job.ended_at = None
        elif failure_count == 0:
            job.status = "completed"
            job.error_message = ""
            job.ended_at = datetime.now(UTC)
        elif success_count == 0:
            job.status = "failed"
            job.error_message = last_error_message or job.error_message
            job.ended_at = datetime.now(UTC)
        else:
            job.status = "partial_failed"
            job.error_message = last_error_message or job.error_message
            job.ended_at = datetime.now(UTC)

        job.updated_at = datetime.now(UTC)
        metadata["success_count"] = success_count
        metadata["failure_count"] = failure_count
        await self._save_job(job.tenant_id or self.default_tenant_id, job)

    def _job_result_from_record(
        self,
        job: BackgroundJobRecord,
    ) -> BackgroundJobResult:
        metadata = job.detail
        file_ids = [
            str(file_id)
            for file_id in metadata.get("file_ids", [])
            if str(file_id).strip()
        ]
        file_results = []
        for item in metadata.get("file_results", []):
            if not isinstance(item, dict):
                continue
            file_results.append(
                BackgroundJobFileResult(
                    file_id=str(item.get("file_id", "")),
                    status=str(item.get("status", "accepted")),
                    row_count=int(item.get("row_count", 0) or 0),
                    started_at=self._as_optional_str(item.get("started_at")),
                    ended_at=self._as_optional_str(item.get("ended_at")),
                    error_message=str(item.get("error_message", "")),
                    metadata=dict(item.get("metadata", {}))
                    if isinstance(item.get("metadata"), dict)
                    else {},
                )
            )
        return BackgroundJobResult(
            job_id=str(job.id or ""),
            tenant_id=job.tenant_id or self.default_tenant_id,
            status=job.status,
            file_ids=file_ids,
            accepted_at=self._as_optional_str(metadata.get("accepted_at")),
            started_at=self._as_optional_str(job.started_at),
            ended_at=self._as_optional_str(job.ended_at),
            error_message=job.error_message,
            file_results=file_results,
        )

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _as_optional_str(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        return text or None
