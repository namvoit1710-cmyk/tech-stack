from typing import Any, List, Optional

import polars as pl

from app.layer2_application.interfaces.source_reader_interface import ISourceReader


class ParquetSourceReader(ISourceReader):
    """``ISourceReader`` for Parquet (used, e.g., by reference key-sets)."""

    formats = ("parquet",)

    def read(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        df = pl.read_parquet(path)
        df.columns = [c.strip() for c in df.columns]
        return df

    def write(self, df: Any, path: str) -> None:
        df.write_parquet(path)
