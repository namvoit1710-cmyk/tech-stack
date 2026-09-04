from __future__ import annotations

from hashlib import sha256

from smart_service_sdk.layer1_domain.entities.spreadsheet_table import (
    SpreadsheetTableProjection,
    SpreadsheetTableRow,
)
from smart_service_sdk.layer2_application.interfaces.chunking_interface import (
    ISpreadsheetSemanticChunker,
)


class SpreadsheetSemanticChunker(ISpreadsheetSemanticChunker):
    def __init__(
        self,
        *,
        max_rows_per_chunk: int = 5,
        max_chunks_per_table: int = 5,
        max_characters_per_chunk: int = 4000,
    ):
        self._max_rows_per_chunk = max_rows_per_chunk
        self._max_chunks_per_table = max_chunks_per_table
        self._max_characters_per_chunk = max_characters_per_chunk

    def chunk(
        self, projections: list[SpreadsheetTableProjection]
    ) -> list[dict[str, object]]:
        chunks: list[dict[str, object]] = []
        for projection in projections:
            chunks.extend(self._chunk_projection(projection))
        return chunks

    def _chunk_projection(
        self, projection: SpreadsheetTableProjection
    ) -> list[dict[str, object]]:
        summary = projection.summary
        base_metadata = {
            "sheet_name": summary.sheet_name,
            "table_name": summary.table_name,
            "numeric_columns": summary.numeric_columns,
            "row_count": summary.row_count,
            "header_paths": summary.metadata.get("header_paths", {}),
            "sheet_anchor": summary.metadata.get("sheet_anchor", summary.sheet_name),
            "table_anchor": summary.metadata.get("table_anchor", summary.table_name),
        }
        chunks: list[dict[str, object]] = []

        schema_content = self._build_schema_content(projection)
        chunks.append(
            {
                "chunk_id": f"{summary.table_name}::schema",
                "document_id": summary.document_id,
                "content": schema_content,
                "metadata": {
                    **base_metadata,
                    "chunk_role": "table_schema",
                },
            }
        )

        remaining_slots = max(self._max_chunks_per_table - 1, 0)
        row_index = 0
        while row_index < len(projection.rows) and remaining_slots > 0:
            window_rows = projection.rows[
                row_index : row_index + self._max_rows_per_chunk
            ]
            fitted_rows = self._fit_rows_within_budget(projection, window_rows)
            if not fitted_rows:
                break

            start_row = fitted_rows[0].row_number
            end_row = fitted_rows[-1].row_number
            chunks.append(
                {
                    "chunk_id": f"{summary.table_name}::rows::{start_row}-{end_row}",
                    "document_id": summary.document_id,
                    "content": self._build_row_window_content(projection, fitted_rows),
                    "metadata": {
                        **base_metadata,
                        "chunk_role": "row_window",
                        "row_number_start": start_row,
                        "row_number_end": end_row,
                        "search_rows": self._search_rows(fitted_rows),
                    },
                }
            )
            row_index += len(fitted_rows)
            remaining_slots -= 1

        self._apply_parent_context_metadata(summary, chunks)
        return chunks

    def _apply_parent_context_metadata(
        self,
        summary,
        chunks: list[dict[str, object]],
    ) -> None:
        if not chunks:
            return

        parent_context_id = self._parent_context_id(summary)
        sibling_count = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            raw_metadata = chunk.get("metadata")
            metadata = (
                {str(key): value for key, value in raw_metadata.items()}
                if isinstance(raw_metadata, dict)
                else {}
            )
            metadata["parent_context_id"] = parent_context_id
            metadata["sibling_index"] = index
            metadata["sibling_count"] = sibling_count
            metadata["anchor_label"] = self._anchor_label(summary, metadata)
            chunk["metadata"] = metadata

    @staticmethod
    def _parent_context_id(summary) -> str:
        digest = sha256(
            f"{summary.document_id}:spreadsheet:{summary.sheet_name}:{summary.table_name}".encode(
                "utf-8"
            )
        ).hexdigest()
        return f"parent-{digest[:16]}"

    @staticmethod
    def _anchor_label(summary, metadata: dict[str, object]) -> str:
        if metadata.get("chunk_role") == "table_schema":
            return f"{summary.table_name}:schema"
        if metadata.get("chunk_role") == "row_window":
            start_row = metadata.get("row_number_start")
            end_row = metadata.get("row_number_end")
            return f"{summary.table_name}:rows:{start_row}-{end_row}"
        return str(summary.table_name)

    def _build_schema_content(self, projection: SpreadsheetTableProjection) -> str:
        summary = projection.summary
        raw_header_paths = summary.metadata.get("header_paths")
        header_paths = (
            {str(key): value for key, value in raw_header_paths.items()}
            if isinstance(raw_header_paths, dict)
            else {}
        )
        lines = [
            f"## Sheet: {summary.sheet_name}",
            "",
            f"Table anchor: {summary.table_name}",
            f"This table has {summary.row_count} rows and {len(summary.column_names)} columns.",
        ]
        if summary.numeric_columns:
            lines.append(f"Numeric columns: {', '.join(summary.numeric_columns)}")
        lines.extend(["", "Columns:"])

        content = "\n".join(lines)
        column_lines_added = 0
        for column_name in summary.column_names:
            raw_path = header_paths.get(column_name)
            path = (
                [str(segment) for segment in raw_path]
                if isinstance(raw_path, list)
                else [column_name]
            )
            candidate = self._append_line(
                content, f"- {column_name}: {' > '.join(path)}"
            )
            if len(candidate) > self._max_characters_per_chunk:
                remaining_columns = len(summary.column_names) - column_lines_added
                if remaining_columns > 0:
                    omission_line = f"- ... {remaining_columns} more columns omitted"
                    content = self._append_line(content, omission_line)
                return self._truncate(content)
            content = candidate
            column_lines_added += 1

        return self._truncate(content)

    def _build_row_window_content(
        self,
        projection: SpreadsheetTableProjection,
        rows: list[SpreadsheetTableRow],
    ) -> str:
        summary = projection.summary
        lines = [
            f"## Sheet: {summary.sheet_name}",
            "",
            f"Table anchor: {summary.table_name}",
            f"Rows {rows[0].row_number}-{rows[-1].row_number} of {summary.row_count}",
        ]
        if summary.column_names:
            lines.extend(
                [
                    "",
                    "| " + " | ".join(summary.column_names) + " |",
                    "| " + " | ".join("---" for _ in summary.column_names) + " |",
                ]
            )
            for row in rows:
                lines.append(
                    "| "
                    + " | ".join(
                        self._stringify(row.payload.get(column_name))
                        for column_name in summary.column_names
                    )
                    + " |"
                )

        return self._truncate("\n".join(lines))

    def _fit_rows_within_budget(
        self,
        projection: SpreadsheetTableProjection,
        rows: list[SpreadsheetTableRow],
    ) -> list[SpreadsheetTableRow]:
        fitted_rows = list(rows)
        while fitted_rows:
            content = self._build_row_window_content(projection, fitted_rows)
            if len(content) <= self._max_characters_per_chunk:
                return fitted_rows
            fitted_rows = fitted_rows[:-1]
        return []

    def _append_line(self, content: str, line: str) -> str:
        if not content:
            return line
        return f"{content}\n{line}"

    def _search_rows(
        self,
        rows: list[SpreadsheetTableRow],
    ) -> list[dict[str, object]]:
        search_rows: list[dict[str, object]] = []
        for row in rows:
            fields = {
                self._normalize_text(key): self._stringify(value)
                for key, value in row.payload.items()
                if self._normalize_text(key)
            }
            normalized_fields = {
                key: self._normalize_text(value)
                for key, value in fields.items()
                if self._normalize_text(value)
            }
            search_rows.append(
                {
                    "row_number": row.row_number,
                    "fields": fields,
                    "normalized_fields": normalized_fields,
                }
            )
        return search_rows

    def _truncate(self, content: str) -> str:
        if len(content) <= self._max_characters_per_chunk:
            return content
        return content[: max(self._max_characters_per_chunk - 3, 0)].rstrip() + "..."

    def _stringify(self, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    def _normalize_text(self, value: object) -> str:
        return " ".join(str(value).strip().lower().split())
