from typing import Protocol, List
from app.layer1_domain.entities.transformation import (
    TransformRule,
    TransformResult,
    RowOperation,
    RowTransformResult,
)


class ITransformerProvider(Protocol):
    async def transform_cloud_file(
        self,
        file_id: str,
        file_format: str,
        output_format: str,
        rules: List[TransformRule],
        version_id: str | None = None,
    ) -> TransformResult: ...


class IRowTransformerProvider(Protocol):
    async def transform_rows(
        self,
        file_path: str,
        file_format: str,
        operations: List[RowOperation],
        use_column_indices: bool = False,
        version_id: str | None = None,
    ) -> RowTransformResult: ...
