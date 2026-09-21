from __future__ import annotations

from typing import Any, Protocol


class IDataFactoryGateway(Protocol):
    async def validate_file(
        self,
        *,
        file_path: str,
        file_format: str = "csv",
        rules: list[dict[str, Any]] | None = None,
        rule_set_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def transform_file(
        self,
        *,
        file_path: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]: ...

    async def inspect_file(
        self,
        *,
        file_path: str,
        file_format: str = "csv",
        sample_size: int = 5,
        sheet_names: list[str] | None = None,
        merge_sheets: bool = False,
    ) -> dict[str, Any]: ...

    async def list_rule_sets(self) -> dict[str, Any]: ...

    async def transform_rows(
        self,
        *,
        file_path: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        use_column_indices: bool = False,
    ) -> dict[str, Any]: ...
