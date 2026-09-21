from __future__ import annotations

from typing import Any, Dict

from agent_sdk import tool

from app.layer2_application.features.query_file_data import query_file_data_impl
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway


def make_query_file_data_tool(*, file_preview_gateway: IFilePreviewGateway):
    @tool(internal=True)
    def query_file_data(
        file_id: str,
        odata_query: str = "",
        top: int = 50,
        extract_fields: str = "",
    ) -> Dict[str, Any]:
        """
        Read/query rows from a file using the File Service OData data endpoint.

        This is a READ-ONLY tool. It does not create, transform, overwrite, or
        upload any file. Use this when the user asks to show/query/read/filter/
        select/sort rows from an existing file and does not ask to create a new
        output file.

        Supported OData options:
        - $filter: row filter, e.g. "$filter=status eq 'active'"
        - $select: selected columns, e.g. "$select=id,name,status"
        - $orderby: sorting, e.g. "$orderby=created_at desc"
        - $top: limit rows
        - $skip: offset rows
        - $count: include count

        Args:
            file_id: Existing File Service file ID.
            odata_query: Query string with OData options. It can start with "?" or not.
            top: Default row limit when odata_query does not include $top. Capped by validation.
            extract_fields: Comma-separated fields, or "*" to return all first-row fields as extracted_fields.v1.

        Returns:
            File Service OData response, plus file_id, odata_query, read_only=true, and optional extracted_fields.v1.
        """
        return query_file_data_impl(
            file_id=file_id,
            odata_query=odata_query,
            file_preview_gateway=file_preview_gateway,
            default_top=top,
            extract_fields=extract_fields,
        )

    return query_file_data


__all__ = ["make_query_file_data_tool"]
