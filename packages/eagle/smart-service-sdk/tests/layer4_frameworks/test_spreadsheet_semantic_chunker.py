from smart_service_sdk.layer1_domain.entities.spreadsheet_table import (
    SpreadsheetTableProjection,
    SpreadsheetTableRow,
    SpreadsheetTableSummary,
)
from smart_service_sdk.layer4_frameworks.chunking.spreadsheet_semantic_chunker import (
    SpreadsheetSemanticChunker,
)


def test_row_window_chunks_include_searchable_field_metadata() -> None:
    chunker = SpreadsheetSemanticChunker(max_rows_per_chunk=1, max_chunks_per_table=2)
    projection = SpreadsheetTableProjection(
        summary=SpreadsheetTableSummary(
            document_id="doc-1",
            sheet_name="Sheet1",
            table_name="Catalog",
            column_names=["Material Name", "Material Description"],
            row_count=1,
        ),
        rows=[
            SpreadsheetTableRow(
                row_number=7,
                payload={
                    "Material Name": "Finished Product: Wireless Earbuds",
                    "Material Description": "Wireless Earbuds",
                },
            )
        ],
    )

    chunks = chunker.chunk([projection])

    row_window = next(chunk for chunk in chunks if chunk["metadata"]["chunk_role"] == "row_window")
    search_rows = row_window["metadata"]["search_rows"]

    assert search_rows[0]["row_number"] == 7
    assert search_rows[0]["fields"]["material name"] == "Finished Product: Wireless Earbuds"
    assert (
        search_rows[0]["normalized_fields"]["material description"]
        == "wireless earbuds"
    )
