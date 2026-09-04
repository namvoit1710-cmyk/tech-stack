from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ReindexFilesCommand:
    file_ids: Sequence[str]
    tenant_id: str | None = None


@dataclass(frozen=True)
class ReindexFilesResult:
    tenant_id: str
    reindexed_file_ids: tuple[str, ...]
    failed_file_ids: tuple[str, ...]


class ReindexFilesUseCase:
    """SA-1451 — re-index already-imported files after a field-config change.

    Changing which fields feed embeddings/graph entities only affects rows
    indexed *after* the change; existing rows keep their old vectors until
    re-indexed. This use case replays the import job for the given files so
    their chunks/graph are rebuilt under the new selection. Re-index = re-run
    the import job for the file (the job runner already rebuilds chunks +
    graph from the current config).
    """

    def __init__(self, *, job_runner, default_tenant_id: str):
        self._job_runner = job_runner
        self._default_tenant_id = default_tenant_id

    async def execute(self, command: ReindexFilesCommand) -> ReindexFilesResult:
        tenant_id = (command.tenant_id or self._default_tenant_id).strip()
        reindexed: list[str] = []
        failed: list[str] = []
        for raw_file_id in command.file_ids:
            file_id = str(raw_file_id).strip()
            if not file_id:
                continue
            try:
                await self._job_runner.run_import_job_for_file(
                    file_id=file_id, tenant_id=tenant_id
                )
                reindexed.append(file_id)
            except Exception:  # noqa: BLE001 - report per-file, never abort the batch
                failed.append(file_id)
        return ReindexFilesResult(
            tenant_id=tenant_id,
            reindexed_file_ids=tuple(reindexed),
            failed_file_ids=tuple(failed),
        )
