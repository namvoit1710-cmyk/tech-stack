from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpreadsheetTableRow:
    row_number: int
    payload: dict[str, object]


@dataclass(frozen=True)
class SpreadsheetTableSummary:
    document_id: str
    sheet_name: str
    table_name: str
    column_names: list[str]
    row_count: int
    start_row: int = 1
    end_row: int = 1
    start_column: int = 1
    end_column: int = 1
    normalized_headers: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)
    summary_text: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SpreadsheetTableProjection:
    summary: SpreadsheetTableSummary
    rows: list[SpreadsheetTableRow] = field(default_factory=list)
