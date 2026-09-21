from agent_sdk import tool
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
import requests

# --- pure function (REAL logic) ---
def _get_first_n_rows_impl(file_id: str, num_rows: int) -> dict:
    settings = AppSettingsAdapter()
    base_url = settings.FILE_URL
    url = f"{base_url}/api/v1/odata/{file_id}/data?$top={num_rows}&$skip=0&$count=true"
    resp = requests.get(url)
    resp.raise_for_status()

    payload = resp.json()
    if isinstance(payload, dict) and payload.get("data"):
        return payload

    download_url = f"{base_url}/api/v1/download/{file_id}"
    download_resp = requests.get(download_url)
    download_resp.raise_for_status()

    first_line = ""
    if download_resp.content:
        first_line = download_resp.content.splitlines()[0].decode("utf-8", errors="replace").strip()

    if not isinstance(payload, dict):
        payload = {"file_id": file_id, "total_matches": 0, "data": []}
    else:
        payload.setdefault("file_id", file_id)

    if first_line:
        payload["total_matches"] = 1
        payload["data"] = [first_line]

    return payload

@tool("get_first_n_rows", internal=True)
def get_first_n_rows(file_id: str, num_rows: int) -> dict:
    """Return the first n rows in a file.

    Args:
        file_id: The ID of the file.
        num_rows: The number of rows in the file.
    Returns:
        A dictionary containing the file ID and the rows.
    """
    return _get_first_n_rows_impl(file_id, num_rows)
