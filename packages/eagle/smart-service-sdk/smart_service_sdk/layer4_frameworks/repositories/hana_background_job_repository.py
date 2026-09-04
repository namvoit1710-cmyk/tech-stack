from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)
from smart_service_sdk.layer2_application.repositories.background_job_repository_interface import (
    IBackgroundJobRepository,
)
from smart_service_sdk.layer4_frameworks.repositories.json_repository_codec import (
    JsonRepositoryCodec,
)

_JSON_CODEC = JsonRepositoryCodec()


class HanaBackgroundJobRepository(IBackgroundJobRepository):
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    async def save(self, job: BackgroundJobRecord) -> BackgroundJobRecord:
        return await asyncio.to_thread(self._save_sync, job)

    async def get(self, tenant_id: str, job_id: str) -> BackgroundJobRecord | None:
        return await asyncio.to_thread(self._get_sync, tenant_id, job_id)

    async def list_by_status(self, status: str) -> list[BackgroundJobRecord]:
        return await asyncio.to_thread(self._list_by_status_sync, status)

    def _save_sync(self, job: BackgroundJobRecord) -> BackgroundJobRecord:
        if not job.id:
            raise ValueError("background job id is required")
        connection = self._connection_factory.acquire()
        cursor = connection.cursor()
        try:
            metadata = dict(job.detail)
            metadata.setdefault("job_type", job.job_type)
            metadata.setdefault("progress", job.progress)
            cursor.execute(
                "UPSERT AE_RAG_BACKGROUND_JOBS (JOB_ID, TENANT_ID, STATUS, CREATED_AT, UPDATED_AT, STARTED_AT, ENDED_AT, ERROR_MESSAGE, METADATA_JSON) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) WITH PRIMARY KEY",
                (
                    job.id,
                    job.tenant_id,
                    job.status,
                    job.created_at,
                    job.updated_at or job.ended_at or job.created_at,
                    job.started_at,
                    job.ended_at,
                    job.error_message,
                    _JSON_CODEC.dump_json(metadata),
                ),
            )
            connection.commit()
            return job
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            self._connection_factory.release(connection)

    def _get_sync(self, tenant_id: str, job_id: str) -> BackgroundJobRecord | None:
        connection = self._connection_factory.acquire()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT JOB_ID, TENANT_ID, STATUS, CREATED_AT, UPDATED_AT, STARTED_AT, ENDED_AT, ERROR_MESSAGE, METADATA_JSON "
                "FROM AE_RAG_BACKGROUND_JOBS WHERE TENANT_ID = ? AND JOB_ID = ?",
                (tenant_id, job_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._record_from_row(row)
        finally:
            cursor.close()
            self._connection_factory.release(connection)

    def _list_by_status_sync(self, status: str) -> list[BackgroundJobRecord]:
        connection = self._connection_factory.acquire()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT JOB_ID, TENANT_ID, STATUS, CREATED_AT, UPDATED_AT, STARTED_AT, ENDED_AT, ERROR_MESSAGE, METADATA_JSON "
                "FROM AE_RAG_BACKGROUND_JOBS WHERE STATUS = ? "
                "ORDER BY CREATED_AT ASC, JOB_ID ASC",
                (status,),
            )
            return [self._record_from_row(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            self._connection_factory.release(connection)

    def _record_from_row(self, row: tuple[object, ...]) -> BackgroundJobRecord:
        detail = _JSON_CODEC.load_json_object(str(row[8] or ""))
        progress = detail.pop("progress", 0.0)
        job_type = str(detail.pop("job_type", "background_job"))
        created_at = self._as_datetime(row[3])
        updated_at = self._as_datetime(row[4])
        ended_at = self._as_datetime(row[6])
        return BackgroundJobRecord(
            id=str(row[0]),
            tenant_id=str(row[1]),
            job_type=job_type,
            status=str(row[2] or ""),
            progress=float(progress or 0.0),
            detail=detail,
            error_message=str(row[7] or ""),
            started_at=self._as_datetime(row[5]),
            ended_at=ended_at,
            created_at=created_at or datetime.now(UTC),
            updated_at=updated_at,
        )

    @staticmethod
    def _as_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        return None
