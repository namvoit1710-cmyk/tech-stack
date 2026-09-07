from typing import Any, List, Optional

import polars as pl

from app.layer2_application.interfaces.source_reader_interface import ISourceReader


class CsvSourceReader(ISourceReader):
    """Reference implementation of ``ISourceReader`` for CSV.

    Values are kept as text (``infer_schema_length=0``) to preserve formatting
    such as leading zeros; a latin-1 retry covers non-UTF-8 exports. Column
    names are trimmed to match the rest of the pipeline's expectations.
    """

    formats = ("csv",)

    def read(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        # A pinned header row means "skip the banner rows above it"; None keeps
        # the default of treating the first line as the header.
        skip_rows = header_row or 0
        try:
            df = pl.read_csv(
                path, infer_schema_length=0, ignore_errors=True, skip_rows=skip_rows
            )
        except Exception:
            df = pl.read_csv(
                path,
                encoding="latin1",
                infer_schema_length=0,
                ignore_errors=True,
                skip_rows=skip_rows,
            )
        df.columns = [c.strip() for c in df.columns]
        return df

    def write(self, df: Any, path: str) -> None:
        df.write_csv(path)
