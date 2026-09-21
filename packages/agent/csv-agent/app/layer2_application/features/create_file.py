import csv
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def sanitize_create_filename(filename: str) -> str:
    """
    Sanitize user-provided filename to prevent path traversal and injection.

    Args:
        filename: User-provided filename

    Returns:
        Safe filename with only alphanumeric, dash, underscore, and dot
    """
    # Remove directory components (prevent path traversal)
    filename = Path(filename).name

    # Remove special characters except alphanumeric, dash, underscore, dot
    filename = re.sub(r"[^\w\.-]", "_", filename)

    # Ensure .csv extension
    if not filename.endswith(".csv"):
        filename += ".csv"

    # Prevent empty filename
    if filename == ".csv":
        filename = "generated_data.csv"

    return filename


def build_llm_prompt(description: str) -> str:
    """
    Build a structured prompt for LLM to generate tabular data.

    Args:
        description: User's description of desired CSV content

    Returns:
        Formatted prompt string
    """
    prompt = f"""Generate tabular data based on this request:
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
    return prompt


def parse_llm_response(response: str) -> Dict[str, Any]:
    """
    Parse and validate LLM response containing CSV data.

    Args:
        response: Raw LLM response string

    Returns:
        Parsed data with columns and rows

    Raises:
        ValueError: If JSON is invalid or missing required fields
    """
    data = json.loads(response)

    if "columns" not in data or "rows" not in data:
        raise ValueError("LLM response missing 'columns' or 'rows' fields")

    if not isinstance(data["columns"], list):
        raise ValueError("'columns' must be a list")

    if not isinstance(data["rows"], list):
        raise ValueError("'rows' must be a list")

    return data


def create_csv_from_data(columns: List[str], rows: List[List[Any]]) -> str:
    """
    Create a temporary CSV file from column headers and row data.

    Args:
        columns: List of column headers
        rows: List of rows (each row is a list of values)

    Returns:
        Path to the created temporary CSV file
    """
    temp_file = tempfile.NamedTemporaryFile(
        mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8"
    )

    try:
        writer = csv.writer(temp_file)
        writer.writerow(columns)

        for row in rows:
            writer.writerow(row)

        file_path = temp_file.name

    finally:
        temp_file.close()

    return file_path


async def create_file_service(
    filename: str, description: str, llm_adapter: Any, file_upload_adapter: Any
) -> Dict[str, Any]:
    """
    Service function for creating CSV files from user descriptions.
    Pure business logic with dependency injection for external services.

    Args:
        filename: Desired filename for the CSV
        description: User description of what the CSV should contain
        llm_adapter: Adapter for LLM service (from layer 4)
        file_upload_adapter: Adapter for file upload service (from layer 4)

    Returns:
        Dictionary with file info or error details
    """
    if not filename or not description:
        return {"error": "Missing filename or description"}

    temp_csv_path = None

    try:
        # Sanitize filename
        filename = sanitize_create_filename(filename)

        # Build LLM prompt
        prompt = build_llm_prompt(description)

        # Call LLM via adapter
        logging.info(f"[CreateFile] Generating CSV structure for: {description}")
        llm_response = await llm_adapter.generate_completion(prompt, "")

        # Parse and validate LLM response
        data = parse_llm_response(llm_response)

        # Create temporary CSV file
        temp_csv_path = create_csv_from_data(data["columns"], data["rows"])

        logging.info(f"[CreateFile] Created temp CSV: {temp_csv_path}")

        # Upload file via adapter
        upload_result = file_upload_adapter.upload_file(temp_csv_path)

        if "error" in upload_result:
            return upload_result

        file_id = upload_result.get("file_id")

        if not file_id:
            return {"error": "File upload failed"}

        return {
            "message": "File created and uploaded successfully",
            "file_id": file_id,
            "filename": filename,
            "rows_count": len(data["rows"]),
            "columns_count": len(data["columns"]),
        }

    except json.JSONDecodeError as e:
        logging.error(
            f"[CreateFile] LLM returned invalid JSON: {str(e)}", exc_info=True
        )
        return {
            "error": "LLM returned invalid JSON",
            "suggestion": "Try rephrasing your description more clearly",
        }

    except ValueError as e:
        logging.error(f"[CreateFile] Validation error: {str(e)}", exc_info=True)
        return {"error": "Invalid data structure from LLM"}

    except Exception as e:
        logging.error(f"[CreateFile] Unexpected error: {str(e)}", exc_info=True)
        return {"error": "Failed to create file"}

    finally:
        # Clean up temporary file
        if temp_csv_path:
            try:
                if os.path.exists(temp_csv_path):
                    os.remove(temp_csv_path)
                    logging.debug(f"[CreateFile] Cleaned up temp file: {temp_csv_path}")
            except OSError as cleanup_error:
                logging.warning(
                    f"[CreateFile] Failed to cleanup temp file: {cleanup_error}"
                )
