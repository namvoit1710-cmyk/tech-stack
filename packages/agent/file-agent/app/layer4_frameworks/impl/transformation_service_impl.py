from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.transformation import (
    TransformationApiResult,
    TransformationBatch,
    TransformationErrorResult,
    TransformationExecutedBatch,
    TransformationExecutionResult,
    TransformationSuccessResult,
)
from app.layer4_frameworks.data_factory.transformation_client import (
    DataFactoryClient,
)


class DataFactoryService:
    MAX_BATCH_SIZE = 100

    def __init__(self, data_factory_client: DataFactoryClient):
        self._data_factory_client = data_factory_client

    def execute(
        self,
        file_id: str,
        rules: list[dict[str, Any]],
    ) -> TransformationExecutionResult:
        batches = self._create_batch(rules)
        if isinstance(batches, TransformationErrorResult):
            return batches

        return self._execute_batch(file_id, batches)

    def _create_batch(
        self,
        rules: list[dict[str, Any]],
    ) -> list[TransformationBatch] | TransformationErrorResult:
        batches: list[TransformationBatch] = []

        for rule in rules:
            item = rule.copy()
            scope = item.get("scope")
            item.pop("scope", None)

            batch = TransformationBatch.from_scope(scope, item)
            if batch is None:
                return TransformationErrorResult(
                    error="Unsupported scope",
                    details=f"Unsupported scope: {scope}",
                )

            should_start_new_batch = (
                not batches
                or not batches[-1].can_accept(batch.type, self.MAX_BATCH_SIZE)
            )

            if should_start_new_batch:
                batches.append(batch)
                continue

            batches[-1].append_item(item)

        return batches

    def _execute_batch(
        self,
        file_id: str,
        batches: list[TransformationBatch],
    ) -> TransformationExecutionResult:
        file_format = file_id.rsplit(".", 1)[-1].lower() if "." in file_id else "csv"
        current_file_id = file_id
        executed_batches: list[TransformationExecutedBatch] = []

        for batch_index, batch in enumerate(batches, start=1):
            payload = batch.to_payload(current_file_id, file_format)

            if batch.type == "column":
                raw_result = self._data_factory_client.execute_column_payload(payload)
            else:
                raw_result = self._data_factory_client.execute_row_payload(payload)

            result = TransformationApiResult.model_validate(raw_result)

            if result.error:
                return TransformationErrorResult(
                    error=result.error,
                    details=result.details,
                    failed_batch_index=batch_index,
                    batch_type=batch.type,
                    last_successful_file_id=current_file_id,
                    payload=payload,
                )

            next_file_id = result.file_id
            if not next_file_id:
                return TransformationErrorResult(
                    error="Transformation API did not return a file_id",
                    failed_batch_index=batch_index,
                    batch_type=batch.type,
                    last_successful_file_id=current_file_id,
                    payload=payload,
                )

            current_file_id = next_file_id
            executed_batches.append(
                TransformationExecutedBatch(
                    batch_index=batch_index,
                    type=batch.type,
                    item_count=batch.item_count,
                    file_id=next_file_id,
                    download_url=result.download_url,
                    message=result.message,
                )
            )

        final_result = executed_batches[-1]
        return TransformationSuccessResult(
            message=f"Executed {len(executed_batches)} transformation batches.",
            file_id=final_result.file_id,
            download_url=final_result.download_url,
            batch_count=len(executed_batches),
            batches=executed_batches,
        )