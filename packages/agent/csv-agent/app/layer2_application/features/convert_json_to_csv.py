import csv
import json
import logging
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.layer2_application.interfaces.file_upload_gateway import IFileUploadGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider


def _normalize_cell_value(value: Any) -> Any:
    """Normalize values for CSV output compatibility."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return value


def _flatten_dict(
    data: Dict[str, Any], parent_key: str = "", sep: str = "."
) -> Dict[str, Any]:
    """Flatten nested dictionaries recursively using the provided separator."""
    flattened: Dict[str, Any] = {}

    for key, value in data.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            flattened.update(_flatten_dict(value, new_key, sep=sep))
        elif isinstance(value, list):
            # Keep arrays in one cell with Python-style string form for compatibility
            # with existing downstream CSV parsing behavior.
            flattened[new_key] = str(value)
        else:
            flattened[new_key] = _normalize_cell_value(value)

    return flattened


def _flatten_records(records: List[Any], sep: str) -> List[Dict[str, Any]]:
    """Flatten records and normalize scalar/list values for CSV export."""
    flattened_rows: List[Dict[str, Any]] = []
    for item in records:
        if isinstance(item, dict):
            flattened_rows.append(_flatten_dict(item, sep=sep))
        else:
            flattened_rows.append({"value": _normalize_cell_value(item)})
    return flattened_rows


# Configuration constants
def sanitize_json_string(json_str: Any) -> Any:
    """
    Clean JSON string by removing/escaping invalid control characters.

    Args:
        json_str: Raw JSON string that may contain control characters

    Returns:
        Cleaned JSON string safe for parsing
    """
    if not isinstance(json_str, str):
        return json_str

    # Strip leading/trailing whitespace
    json_str = json_str.strip()

    # Remove NULL bytes
    json_str = json_str.replace("\x00", "")

    def replace_control_chars(match):
        char = match.group(0)
        # Convert to unicode escape
        return f"\\u{ord(char):04x}"

    json_str = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", replace_control_chars, json_str)

    return json_str


def extract_first_valid_json(json_str: str) -> str:
    """
    Extract the first valid JSON object or array from a string that may contain extra data.
    Handles cases where there's trailing text after a valid JSON structure.

    Args:
        json_str: String that may contain JSON followed by extra data

    Returns:
        First valid JSON object/array as string

    Raises:
        ValueError: If no valid JSON found
    """
    if not json_str:
        raise ValueError("Empty JSON string")

    json_str = json_str.strip()

    # Try to find the end of the first complete JSON structure
    # Use a simple bracket counting approach
    if not json_str:
        raise ValueError("Empty JSON string after strip")

    if json_str[0] == "{":
        open_char, close_char = "{", "}"
    elif json_str[0] == "[":
        open_char, close_char = "[", "]"
    else:
        return json_str

    depth = 0
    in_string = False
    escape_next = False

    for i, char in enumerate(json_str):
        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if not in_string:
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    # Found complete JSON structure
                    return json_str[: i + 1]

    # If we get here, didn't find complete structure, return original
    return json_str


def separate_nested_structures(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically separate nested structures into main data and to_* groups.
    - Fields with 'to_*' prefix → separate groups with their own headers
    - Other nested objects/primitives → flattened into MAIN DATA

    Args:
        data: Single data object (dict)

    """
    main_data = {}
    to_groups = {}

    for key, value in data.items():
        if key.startswith("to_"):
            # This is a to_* field = separate group
            to_groups[key] = value
        else:
            # This goes into main data (will be flattened if nested)
            main_data[key] = value

    return {"main_data": main_data, "to_groups": to_groups}


def flatten_json(
    nested_json: Any, max_level: int | None = None
) -> List[Dict[str, Any]]:
    """
    Flatten nested JSON into a list of flat dictionaries suitable for CSV conversion.

    Args:
        nested_json: Can be a dict, list of dicts, or nested structure
        max_level: Maximum depth for flattening (None = unlimited)

    Returns:
        List of flattened dictionaries
    """
    try:
        # If it's a string, sanitize and parse it first
        if isinstance(nested_json, str):
            logging.info("[FlattenJSON] Sanitizing JSON string...")
            nested_json = sanitize_json_string(nested_json)

            # Validate JSON before parsing
            if not nested_json:
                raise ValueError("Empty JSON string after sanitization")

            # Try to extract first valid JSON if there's extra data
            try:
                nested_json = extract_first_valid_json(nested_json)
                nested_json = json.loads(nested_json)
            except json.JSONDecodeError as e:
                error_pos = e.pos if hasattr(e, "pos") else 0
                context_start = max(0, error_pos - 50)
                context_end = min(len(nested_json), error_pos + 50)
                context = nested_json[context_start:context_end]
                logging.error(
                    f"[FlattenJSON] JSON parse error at position {error_pos}: {e.msg}"
                )
                logging.error(f"[FlattenJSON] Context: ...{context}...")
                raise ValueError(f"Invalid JSON at position {error_pos}: {e.msg}")

        # If it's a dict with common API response wrappers, extract the data array
        if isinstance(nested_json, dict):
            # Direct key access instead of loop for performance
            if "data" in nested_json and isinstance(nested_json["data"], list):
                logging.info("[FlattenJSON] Extracting array from 'data' field")
                nested_json = nested_json["data"]
            elif "results" in nested_json and isinstance(nested_json["results"], list):
                logging.info("[FlattenJSON] Extracting array from 'results' field")
                nested_json = nested_json["results"]
            elif "items" in nested_json and isinstance(nested_json["items"], list):
                logging.info("[FlattenJSON] Extracting array from 'items' field")
                nested_json = nested_json["items"]
            else:
                # If no wrapper found, wrap single dict in a list
                nested_json = [nested_json]

        if not isinstance(nested_json, list):
            nested_json = [nested_json]

        flattened = _flatten_records(nested_json, sep="_")
        if max_level is not None:
            # Keep signature compatibility; depth limiting is not required for current callers.
            return flattened
        return flattened

    except ValueError as e:
        # Re-raise ValueError with original message
        logging.error(f"[FlattenJSON] Validation error: {str(e)}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"[FlattenJSON] JSON decode error: {str(e)}")
        raise ValueError(f"Invalid JSON format: {str(e)}")
    except Exception as e:
        logging.error(f"[FlattenJSON] Unexpected error: {str(e)}")
        raise


def create_csv_from_json(data: List[Dict[str, Any]]) -> str:
    """
    Create a temporary CSV file from flattened JSON data.

    Args:
        data: List of flattened dictionaries
        filename: Desired filename (optional)

    Returns:
        Path to the created temporary CSV file
    """
    temp_file = tempfile.NamedTemporaryFile(
        mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8"
    )

    if not data:
        temp_file.close()
        return temp_file.name

    # Build headers from all rows while preserving first-seen column order.
    headers = list(dict.fromkeys(key for row in data for key in row.keys()))

    writer = csv.DictWriter(temp_file, fieldnames=headers)
    writer.writeheader()

    # Use writerows for bulk writing (faster than loop)
    writer.writerows(data)

    file_path = temp_file.name
    temp_file.close()

    return file_path


def process_single_group(
    group_name: str, group_data: Any
) -> Tuple[str, List[Dict[str, Any]], List[str], List[str], bool]:
    """
    Process a single to_* group in parallel.
    Creates flat column names with index for list items.

    Args:
        group_name: Name of the group (e.g., 'to_Description')
        group_data: Data for this group (dict or list)

    Returns:
        Tuple of (group_name, flattened_rows, source_columns, flat_column_names, is_multi_item_list)

    Raises:
        ValueError: If group processing fails
    """
    # Handle dict vs list
    is_list = isinstance(group_data, list)

    if isinstance(group_data, dict):
        group_data = [group_data]
    elif not isinstance(group_data, list):
        raise ValueError(f"Group '{group_name}' has invalid type: {type(group_data)}")

    if not group_data:
        # Keep empty to_* groups as a single blank column instead of failing conversion.
        return (group_name, [{"": ""}], [""], [group_name], False)

    # Flatten nested objects within the group using dot separator
    # This ensures nested fields like to_ArtPlntForecastData.plant (not to_ArtPlntForecastData_plant)
    flattened_rows = _flatten_records(group_data, sep=".")

    # Build deterministic first-seen column order.
    source_columns = list(
        dict.fromkeys(key for row in flattened_rows for key in row.keys())
    )

    # Validate columns exist after normalization
    if len(source_columns) == 0:
        # Preserve group presence in CSV even when payload has no concrete fields.
        return (group_name, [{"": ""}], [""], [group_name], False)

    # Build flat column names
    flat_column_names = []
    is_multi_item = is_list and len(group_data) > 1

    if is_multi_item:
        # For list with multiple items: create indexed columns like to_Description.article0, to_Description.article1
        # Order: field0, field1, field2... for ALL rows
        for row_idx in range(len(flattened_rows)):
            for col in source_columns:
                flat_name = f"{group_name}.{col}{row_idx}"
                flat_column_names.append(flat_name)
    else:
        # For dict or single-item list: simple dot notation like to_Description.article
        for col in source_columns:
            flat_name = f"{group_name}.{col}"
            flat_column_names.append(flat_name)

    return (
        group_name,
        flattened_rows,
        source_columns,
        flat_column_names,
        is_multi_item,
    )


def create_csv_with_grouped_headers(
    data: Dict[str, Any],
    filename: str = "output.csv",
    *,
    settings_provider: IRuntimeSettingsProvider,
) -> str:
    """
    Create a CSV file with single-row flat headers.
    Uses dot notation: main_table.field, to_Name.field, to_Name.field0 for lists.

    Args:
        data: Dictionary with primitives and nested structures
        filename: Output filename (unused - for API consistency)

    Returns:
        Path to the created CSV file
    """
    temp_file = tempfile.NamedTemporaryFile(
        mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8"
    )

    writer = csv.writer(temp_file)

    # Separate main data and to_* groups
    separated = separate_nested_structures(data)
    main_data = separated["main_data"]
    to_groups = separated["to_groups"]

    # Step 1: Build flat column names for main data with 'main_table.' prefix
    if main_data:
        # Use dot separator for nested fields (e.g., field.subfield not field_subfield)
        main_flat = _flatten_dict(main_data, sep=".")
        # Add main_table. prefix to all columns
        main_column_names = [f"main_table.{col}" for col in main_flat.keys()]
    else:
        main_flat = {}
        main_column_names = []

    # Step 2: Process each to_* group IN PARALLEL
    group_dfs = []
    all_group_column_names = []

    if to_groups:
        # Determine if parallel processing is worth it (overhead vs benefit)
        use_parallel = len(to_groups) >= settings_provider.get_min_groups_for_parallel()

        if use_parallel:
            max_workers = min(
                len(to_groups), settings_provider.get_default_max_workers()
            )
            logging.info(
                f"[GroupedHeaders] Processing {len(to_groups)} groups in parallel with {max_workers} workers"
            )

            # Use ThreadPoolExecutor to parallelize independent group flattening.
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all group processing tasks
                future_to_group = {
                    executor.submit(
                        process_single_group, group_name, group_data
                    ): group_name
                    for group_name, group_data in to_groups.items()
                }

                # Collect results preserving original order
                results = {}
                for future in as_completed(future_to_group):
                    group_name = future_to_group[future]
                    try:
                        results[group_name] = future.result()
                    except Exception as e:
                        logging.error(
                            f"[GroupedHeaders] Failed to process group '{group_name}': {e}"
                        )
                        raise ValueError(
                            f"Failed to process group '{group_name}': {str(e)}"
                        )

                # Process results in original dictionary order (Python 3.7+ preserves dict order)
                for group_name in to_groups.keys():
                    (
                        result_name,
                        result_rows,
                        source_columns,
                        flat_names,
                        is_multi_item,
                    ) = results[group_name]
                    group_dfs.append(
                        (result_name, result_rows, source_columns, is_multi_item)
                    )
                    all_group_column_names.extend(flat_names)
        else:
            # Sequential processing for single group (no overhead)
            logging.info(
                f"[GroupedHeaders] Processing {len(to_groups)} group(s) sequentially"
            )
            for group_name, group_data in to_groups.items():
                try:
                    (
                        result_name,
                        result_rows,
                        source_columns,
                        flat_names,
                        is_multi_item,
                    ) = process_single_group(group_name, group_data)
                    group_dfs.append(
                        (result_name, result_rows, source_columns, is_multi_item)
                    )
                    all_group_column_names.extend(flat_names)
                except Exception as e:
                    logging.error(
                        f"[GroupedHeaders] Failed to process group '{group_name}': {e}"
                    )
                    raise ValueError(
                        f"Failed to process group '{group_name}': {str(e)}"
                    )

    # Step 3: Build combined data - optimized for performance
    # Pre-compute header (concatenation is faster than extend)
    all_column_names = main_column_names + all_group_column_names

    if not all_column_names:
        temp_file.close()
        return temp_file.name

    # Write single header row
    writer.writerow(all_column_names)

    # Step 4: Build data row efficiently using list comprehension + chain
    main_data_values = list(main_flat.values()) if main_flat else []

    # Process all groups with list comprehension (much faster than loop)
    group_data_values = [
        # Multi-item list: flatten all rows using nested comprehension
        [row.get(col, "") for row in group_rows for col in source_columns]
        if is_multi_item
        # Single item: first row by deterministic source column order
        else [group_rows[0].get(col, "") for col in source_columns]
        for _, group_rows, source_columns, is_multi_item in group_dfs
    ]

    # Single flatten using chain (fastest method)
    combined_row = list(chain(main_data_values, *group_data_values))

    writer.writerow(combined_row)

    file_path = temp_file.name
    temp_file.close()

    return file_path


def sanitize_filename(
    filename: str, default_filename: str = "converted_data.csv"
) -> str:
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
        filename = default_filename

    return filename


def convert_json_to_csv_service(
    json_data: Any,
    filename: str,
    *,
    file_upload_gateway: IFileUploadGateway,
    settings_provider: IRuntimeSettingsProvider,
) -> dict:
    """
    Service function for converting JSON to CSV and uploading to file service.
    Pure business logic with dependency injection for external services.

    Args:
        json_data: Nested JSON string or structure to convert
        filename: Desired filename for the CSV
        file_upload_gateway: Optional file upload gateway from layer 2 interface

    Returns:
        dict with conversion result and file info
    """
    if not json_data:
        return {"error": "Missing json_data"}

    # Use default filename if not provided or empty
    if not filename:
        filename = settings_provider.get_default_filename()

    temp_csv_path = None

    try:
        # Keep a local Any-typed payload so the runtime still accepts strings,
        # dicts, lists, and JSON scalar values while strict checkers can narrow
        # grouped-header calls to real dictionaries.
        payload: Any = json_data

        # Sanitize filename to prevent injection attacks
        filename = sanitize_filename(
            filename, default_filename=settings_provider.get_default_filename()
        )

        logging.info(f"[ConvertJSON] Converting JSON to CSV with filename: {filename}")

        # Parse JSON if string
        if isinstance(payload, str):
            payload = sanitize_json_string(payload)

            # Validate before parsing
            if not payload:
                return {"error": "Empty JSON string after sanitization"}

            try:
                # Try to extract first valid JSON if there's extra data
                payload = extract_first_valid_json(payload)
                payload = json.loads(payload)
            except json.JSONDecodeError as e:
                error_pos = e.pos if hasattr(e, "pos") else 0
                logging.error(
                    f"[ConvertJSON] JSON parse error at position {error_pos}: {e.msg}",
                    exc_info=True,
                )
                return {
                    "error": "Invalid JSON format",
                    "suggestion": "Check for missing brackets, trailing commas, or extra data after JSON object.",
                }

        # Detect if this has 'to_*' fields
        has_nested_structures = isinstance(payload, dict) and any(
            str(key).startswith("to_") for key in payload.keys()
        )

        # Auto-select format based on data structure
        if has_nested_structures and isinstance(payload, dict):
            # Use grouped headers with multi-level headers for 'to_*' fields
            logging.info(
                "[ConvertJSON] Detected 'to_*' fields - using grouped headers format"
            )
            grouped_payload: dict[str, Any] = payload
            temp_csv_path = create_csv_with_grouped_headers(
                grouped_payload, filename, settings_provider=settings_provider
            )

            # Count sections and rows
            separated = separate_nested_structures(grouped_payload)
            sections_count = len(separated["to_groups"])

            # Calculate actual rows (denormalized count)
            max_nested_rows = 1
            for group_data in separated["to_groups"].values():
                if isinstance(group_data, list):
                    max_nested_rows = max(max_nested_rows, len(group_data))

            rows_count = max_nested_rows

            # Calculate actual column count after flattening (use dot separator to match processing)
            if separated["main_data"]:
                columns_count = len(
                    _flatten_dict(separated["main_data"], sep=".").keys()
                )
            else:
                columns_count = 0

        else:
            # Standard flat CSV
            logging.info("[ConvertJSON] Using standard flat CSV format")
            flattened_data = flatten_json(payload, max_level=None)

            if not flattened_data:
                return {
                    "error": "No data to convert",
                    "message": "JSON data resulted in empty dataset",
                }

            # Count columns for user info
            columns_count = (
                len(dict.fromkeys(key for row in flattened_data for key in row.keys()))
                if flattened_data
                else 0
            )
            rows_count = len(flattened_data)
            sections_count = 0
            logging.info(
                f"[ConvertJSON] Generated {rows_count} rows with {columns_count} columns"
            )

            # Create temporary CSV file
            temp_csv_path = create_csv_from_json(flattened_data)

        # Upload file using adapter
        upload_result = file_upload_gateway.upload_file(temp_csv_path)

        response_data = {
            "message": "JSON converted to CSV and uploaded successfully",
            "file_id": upload_result["file_id"],
            "download_url": upload_result["download_url"],
            "rows_converted": rows_count,
            "columns_count": columns_count,
        }

        # Add sections info if has nested structures
        if has_nested_structures:
            response_data["sections"] = sections_count

        return response_data

    except json.JSONDecodeError as e:
        logging.error(f"[ConvertJSON] JSON parse error: {str(e)}", exc_info=True)
        return {
            "error": "Invalid JSON format",
            "suggestion": "Ensure JSON is properly formatted and control characters are escaped",
        }

    except ValueError as e:
        logging.error(f"[ConvertJSON] Value error: {str(e)}", exc_info=True)
        return {"error": "JSON processing error"}

    except Exception as e:
        logging.error(f"[ConvertJSON] Unexpected error: {str(e)}", exc_info=True)
        return {"error": "Conversion failed"}

    finally:
        # Clean up temporary file - always try to remove if it exists
        if temp_csv_path:
            try:
                if os.path.exists(temp_csv_path):
                    os.remove(temp_csv_path)
                    logging.debug(
                        f"[ConvertJSON] Cleaned up temp file: {temp_csv_path}"
                    )
            except OSError as cleanup_error:
                logging.warning(
                    f"[ConvertJSON] Failed to cleanup temp file: {cleanup_error}"
                )
