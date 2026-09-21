from agent_sdk import tool, OpenAIService
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
import requests
import csv
import json
import os
import tempfile


def create_temp_csv(headers, rows):
    """
    Create a temporary CSV file with headers and rows.
    The file can be deleted after use.
    """

    temp_file = tempfile.NamedTemporaryFile(
        mode='w',
        newline='',
        suffix='.csv',
        delete=False,
        encoding='utf-8'
    )

    writer = csv.writer(temp_file)
    writer.writerow(headers)

    for row in rows:
        writer.writerow(row)

    file_path = temp_file.name
    temp_file.close()

    return file_path


@tool("create_file", internal=True)
async def create_file(filename: str, description: str) -> dict:
    """Create a CSV file based on user requirements. 
    Args: filename: Name of the CSV file. 
    description: User description of what the CSV should contain. 
     Returns:
        {
            "file_id": str,
            "download_url": str,
            "message": str
        }
    """

    try:
        settings = AppSettingsAdapter()
        url = settings.FILE_URL.rstrip("/")

        llm = OpenAIService()

        # ✅ Explicit JSON instruction (fixes your earlier error)
        prompt = f"""
Generate tabular data based on this request:
{description}

Return ONLY valid JSON.

Format:
{{
  "columns": ["col1", "col2"],
  "rows": [
    ["value1", "value2"],
    ["value3", "value4"]
  ]
}}
"""

        # 🔥 Call LLM
        response = await llm.get_chat_completion(prompt, "")

        # ✅ Parse JSON safely
        data = json.loads(response)

        if "columns" not in data or "rows" not in data:
            raise ValueError("Invalid JSON structure from LLM")

        temp_csv_path = create_temp_csv(data["columns"], data["rows"])

        # Ensure correct extension
        if not filename.endswith(".csv"):
            filename += ".csv"

        try:
            with open(temp_csv_path, "rb") as csv_file:
                resp = requests.post(
                    f"{url}/api/v1/upload",
                    params={"filename": filename},
                    data=csv_file,
                    headers={"Content-Type": "text/csv", "accept": "application/json"},
                )
                resp.raise_for_status()

            # Parse response
            result = resp.json()
            file_id = result.get("file_id")
        finally:
            if os.path.exists(temp_csv_path):
                os.remove(temp_csv_path)

        return {
            "message": "File uploaded successfully",
            "download_url": f"{url}/api/v1/download/{file_id}",
            "file_id": file_id,
        }

    except json.JSONDecodeError:
        return {
            "message": "LLM returned invalid JSON",
            "error": "Failed to parse JSON response",
        }

    except Exception as e:
        return {
            "message": "An error occurred",
            "error": str(e),
        }