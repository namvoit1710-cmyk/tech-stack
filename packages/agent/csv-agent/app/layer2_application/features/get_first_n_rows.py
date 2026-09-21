import csv
import io
import logging

from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway

logger = logging.getLogger(__name__)


def get_first_n_rows_impl(
    file_id: str,
    num_rows: int,
    *,
    file_preview_gateway: IFilePreviewGateway,
) -> dict:
    """Internal implementation for getting first N rows."""
    payload = file_preview_gateway.get_odata_preview(file_id, num_rows)
    if isinstance(payload, dict) and payload.get("data"):
        return payload

    download_content = file_preview_gateway.download_file_content(file_id)

    rows = []
    safe_num_rows = 0
    try:
        safe_num_rows = max(0, int(num_rows))
    except (TypeError, ValueError):
        safe_num_rows = 0

    if download_content:
        content_text = download_content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(content_text))
        for index, row in enumerate(reader):
            if index >= safe_num_rows:
                break
            rows.append(row)

    if not isinstance(payload, dict):
        payload = {"file_id": file_id, "total_matches": 0, "data": []}
    else:
        payload.setdefault("file_id", file_id)

    if rows:
        payload["total_matches"] = len(rows)
        payload["data"] = rows

    return payload
