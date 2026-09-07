from typing import Any, List, Optional, Protocol, Tuple


class ISourceReader(Protocol):
    """A single source-format strategy: reads a local file of one format into a
    Polars DataFrame and writes a DataFrame back out.

    One implementation per format (csv, json, parquet, excel, ...), registered in
    the ``SourceReaderRegistry``. The storage provider and all validation /
    transformation logic operate on the returned DataFrame and never branch on
    format, so supporting a new format — or a non-file source such as a SAP
    export that materializes to a DataFrame — is a new implementation plus one
    registration, with no change to the provider or any use case.

    ``read`` accepts the multi-sheet kwargs (``sheet_names``, ``merge_sheets``,
    ``add_sheet_name_column``) and ``header_row``; formats they do not apply to
    ignore them.

    ``header_row`` is the 0-based index of the row holding the column names.
    ``None`` keeps each format's default (the first row). Templates that carry
    banner/section rows above the real header — SMDG mass-upload workbooks put
    the field names on row 3, i.e. ``header_row=2`` — need it, otherwise every
    column reads back as ``__UNNAMED__n`` and the banner rows arrive as data.
    """

    #: Format keys this reader handles, normalized (no leading dot, lower-case).
    formats: Tuple[str, ...]

    def read(
        self,
        path: str,
        *,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any: ...

    def write(self, df: Any, path: str) -> None: ...
