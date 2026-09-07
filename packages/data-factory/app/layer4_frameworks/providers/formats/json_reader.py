from typing import Any, List, Optional

import polars as pl

from app.layer2_application.interfaces.source_reader_interface import ISourceReader


class JsonSourceReader(ISourceReader):
    """``ISourceReader`` for JSON."""

    formats = ("json",)

    def read(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        df = pl.read_json(path)
        df.columns = [c.strip() for c in df.columns]
        return df

    def write(self, df: Any, path: str) -> None:
        df.write_json(path)
