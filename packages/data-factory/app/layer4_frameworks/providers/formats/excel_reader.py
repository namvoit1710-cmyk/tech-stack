from typing import Any, Dict, List, Optional

import polars as pl

from app.layer2_application.interfaces.source_reader_interface import ISourceReader


class ExcelSourceReader(ISourceReader):
    """``ISourceReader`` for Excel workbooks (xlsx/xls).

    Handles single-sheet, first-of-many, and multi-sheet-merge reads. This is
    where all sheet-selection/merge logic lives, keeping the storage provider
    format-agnostic.

    ``header_row`` (0-based) selects the row holding the column names. Business
    templates routinely stack banner rows above it — an SMDG mass-upload
    workbook uses row 1 for the logical table name, row 2 for the section, and
    row 3 for the fields, i.e. ``header_row=2``. Without it Polars takes row 1
    as the header and the sheet reads back as ``__UNNAMED__n`` columns with the
    two banner rows counted as data.
    """

    formats = ("xlsx", "xls")

    def read(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        read_options = self._build_read_options(header_row)

        if merge_sheets:
            return self._read_and_merge_sheets(
                path,
                sheet_names=sheet_names,
                add_sheet_name_column=add_sheet_name_column,
                read_options=read_options,
            )

        if sheet_names:
            if len(sheet_names) == 1:
                df = pl.read_excel(path, sheet_name=sheet_names[0], **read_options)
            else:
                sheet_frames = pl.read_excel(path, sheet_name=sheet_names, **read_options)
                if isinstance(sheet_frames, dict):
                    # Multiple sheets selected without merge_sheets=True: fall
                    # back to the first selected sheet.
                    first_sheet_name = next(iter(sheet_frames.keys()))
                    df = sheet_frames[first_sheet_name]
                else:
                    df = sheet_frames
        else:
            df = pl.read_excel(path, **read_options)

        df.columns = [c.strip() for c in df.columns]
        return df

    def write(self, df: Any, path: str) -> None:
        df.write_excel(path)

    @staticmethod
    def _build_read_options(header_row: Optional[int]) -> Dict[str, Any]:
        """Kwargs for ``pl.read_excel``. Empty unless a header row is pinned, so
        the default read path stays byte-for-byte what it was."""
        if header_row is None:
            return {}
        if header_row < 0:
            raise ValueError(
                f"header_row must be a 0-based row index >= 0, got {header_row}."
            )
        return {"read_options": {"header_row": header_row}}

    def _read_and_merge_sheets(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        add_sheet_name_column: bool = False,
        read_options: Optional[Dict[str, Any]] = None,
    ) -> pl.DataFrame:
        read_options = read_options or {}
        if sheet_names:
            workbook = pl.read_excel(path, sheet_name=sheet_names, **read_options)
        else:
            workbook = pl.read_excel(path, sheet_id=0, **read_options)

        if isinstance(workbook, pl.DataFrame):
            workbook.columns = [c.strip() for c in workbook.columns]
            if add_sheet_name_column and "source_sheet" not in workbook.columns:
                workbook = workbook.with_columns(pl.lit("Sheet1").alias("source_sheet"))
            return workbook

        if not isinstance(workbook, dict) or not workbook:
            return pl.DataFrame()

        normalized_frames: List[pl.DataFrame] = []
        for sheet_name, df in workbook.items():
            current = df
            current.columns = [c.strip() for c in current.columns]
            if add_sheet_name_column and "source_sheet" not in current.columns:
                current = current.with_columns(pl.lit(sheet_name).alias("source_sheet"))
            normalized_frames.append(current)

        if not normalized_frames:
            return pl.DataFrame()

        return pl.concat(normalized_frames, how="diagonal_relaxed")
