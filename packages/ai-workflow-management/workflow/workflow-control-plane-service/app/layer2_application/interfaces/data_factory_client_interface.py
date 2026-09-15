from typing import Any, Protocol


class IDataFactoryClient(Protocol):
    """Port for data transformation via the data-factory service."""

    async def transform_rows(
        self,
        file_id: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Row-level insert/delete/update operations.

        ``version_id`` is optional; when omitted the factory reads the latest
        file version, otherwise the read/write is pinned to that version.

        The factory mutates the file in place, so the response addresses the
        new revision by ``version_id`` (encoded in ``download_url``) rather
        than a fresh ``file_id``. Returns dict with keys: success, file_id
        (stable identity), version_id (new revision), affected_rows,
        total_rows, message, download_url.
        """
        ...

    async def transform_data(
        self,
        file_id: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]:
        """Column-level transformations (filter, mapping, rename, drop, case_when, etc.).

        Returns dict with keys: success, result, message.
        """
        ...
