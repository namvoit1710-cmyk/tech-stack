import json
import logging
from typing import Optional

from app.layer1_domain.exceptions import (
    InvalidInputError,
    LLMServiceError,
    MCPCommunicationError,
    TransformationError,
)
from app.layer2_application.features.get_first_n_rows import get_first_n_rows_impl
from app.layer2_application.interfaces.data_factory_gateway import IDataFactoryGateway
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)
from app.layer2_application.validators.input_validators import (
    validate_file_id,
    validate_non_empty_string,
)

logger = logging.getLogger(__name__)


def _normalize_mcp_result(result: dict) -> dict:
    """Normalize MCP payload when `result` is a JSON string or nested wrapper."""
    result_obj = result.get("result")
    if isinstance(result_obj, str):
        try:
            parsed = json.loads(result_obj)
            result["result"] = parsed
        except json.JSONDecodeError:
            pass
    return result


# NOTE: This is the IMPLEMENTATION function, NOT registered as tool
# The wrapper in tools/transform_row.py handles tool registration
async def transform_row(
    long_input: str | None = None,
    file_id: str | None = None,
    data_factory_gateway: Optional[IDataFactoryGateway] = None,
    structured_llm_gateway: Optional[IStructuredLLMGateway] = None,
    file_preview_gateway: Optional[IFilePreviewGateway] = None,
    settings_provider: Optional[IRuntimeSettingsProvider] = None,
) -> dict:
    """
    Transform rows in a file using natural language instructions.

    Capabilities:
    - Delete rows (by index, condition, or exact values)
    - Insert rows at the start of the CSV file or at a specific index

    Input:
    - long_input: natural language instruction (required)
    - file_id: input file identifier (required)
    - structured_llm_gateway: Optional structured LLM gateway instance (for dependency injection)

    Batching:
    - Supports multiple row operations in a single call

    Rules:
    - Only "insert" and "delete" operations are allowed
    - Maximum 100 operations per request
    - Column names must match the file schema

    OUTPUT BEHAVIOR (IMPORTANT):
    - Always creates a NEW file after transformations
    - Returns a new file_id representing the latest version
    - This file_id MUST be used for subsequent steps if chaining operations

    Returns:
    {
        "file_id": str,
        "download_url": str,
        "message": str
    }

    Raises:
        InvalidInputError: If inputs fail validation
        TransformationError: If transformation logic fails
        MCPCommunicationError: If MCP server communication fails
        LLMServiceError: If LLM service fails
    """

    # Validate inputs - raises InvalidInputError on failure
    try:
        file_id = validate_file_id(file_id, "file_id")
        long_input = validate_non_empty_string(
            long_input, "long_input", max_length=10000
        )
    except InvalidInputError:
        raise

    if structured_llm_gateway is None:
        raise LLMServiceError(
            "Structured LLM gateway is required for row transformation"
        )
    if data_factory_gateway is None:
        raise TransformationError(
            "dependency_injection", "Data Factory gateway is required"
        )
    if file_preview_gateway is None:
        raise TransformationError(
            "dependency_injection", "File preview gateway is required"
        )
    if settings_provider is None:
        raise TransformationError(
            "dependency_injection", "Runtime settings provider is required"
        )

    try:
        preview_rows = get_first_n_rows_impl(
            file_id, 10, file_preview_gateway=file_preview_gateway
        )
    except Exception as e:
        raise TransformationError(
            "get_preview_rows", f"Failed to fetch preview rows: {str(e)}"
        ) from e

    system_prompt = """
You are a data transformation assistant. Convert user requests into VALID JSON for row transformation API.

OUTPUT: ONLY valid JSON. NO explanation, markdown, or extra text.

SCHEMA:
{
  "file_path": "<string>",
  "file_format": "csv",
  "operations": [{
    "operation": "insert" | "delete",
    "row_identifier": {
      "type": "index" | "condition" | "values",
      "index": <int>,                           // for type="index"
      "expression": "<polars expression>",      // for type="condition"
      "match_values": {"column": "value"}       // for type="values"
    },
    "position": "before" | "after" | "at_index",  // for insert
    "data": {"column": "value"}                   // for insert
  }]
}

OPERATIONS: Only "insert" and "delete". Max 100 operations.

ROW IDENTIFIER (must match EXACTLY ONE row):
1. type="index": Use when user specifies row number
   - User rows are 1-based (row 1 = index 0, row 2 = index 1)
   - Default fallback: index=0 with "at_index"

2. type="values": Exact column matches
   - Must uniquely identify one row
   - Use multiple columns if needed

3. type="condition": Polars expression
   - Must match exactly one row
   - Use == and & for precision
   - Avoid broad conditions (>, <)

HEADER DETECTION:
- Header may NOT be first row
- Infer from structure: column-like names, stability, consistency
- Column names are CASE-SENSITIVE and immutable
- "Id" ≠ "id" ≠ "ID"

TYPE HANDLING:
- Match actual data format in file
- CSV values are often strings: pl.col("age") == "77" not 77

COLUMN RULES:
- ONLY use columns from inferred header
- Map fuzzy user input to closest header match
- DO NOT modify casing or invent columns

INSERT RULES:
- "data" required
- Fill missing values with "UNKNOWN" for existing columns
- Default position: at_index 0 (top of file)

DELETE RULES:
- Only row_identifier required

VALIDATION:
- Verify columns exist in header with correct casing
- If invalid: return {"file_path": "<file_path>", "file_format": "csv", "operations": [], "error": "<reason>"}

FINAL: Return ONLY JSON. No other output.
"""

    user_prompt = f"""
User request: {long_input}
File ID: {file_id}
Sample rows: {json.dumps(preview_rows)}

Set file_path = file_id in output.
"""

    try:
        # Step 1: Generate validated structured output through the layer-4 gateway
        payload = await structured_llm_gateway.generate_row_transform_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            file_id=file_id,
        )

    except LLMServiceError:
        raise  # Re-raise LLM errors as-is
    except Exception as e:
        raise TransformationError(
            "llm_processing",
            f"Unexpected error during LLM structured output generation: {str(e)}",
            {"payload": payload if "payload" in locals() else None},
        ) from e

    # Step 3: Call Data Factory via MCP
    try:
        logger.info(f"[Row] Calling MCP with payload: {json.dumps(payload)}")

        # Call MCP tool (returns dict)
        result = await data_factory_gateway.transform_rows(
            file_path=payload["file_path"],
            operations=payload["operations"],
            file_format=payload.get("file_format", "csv"),
            use_column_indices=payload.get("use_column_indices", False),
        )

        result = _normalize_mcp_result(result)

        # Log MCP response for debugging
        logger.info(f"[Row] MCP Response Keys: {list(result.keys())}")
        logger.info(f"[Row] MCP Success: {result.get('success')}")
        logger.info(f"[Row] MCP Message: {result.get('message')}")

        # Log result object if exists
        if "result" in result and isinstance(result["result"], dict):
            logger.info(f"[Row] Result Keys: {list(result['result'].keys())}")
            logger.debug(
                f"[Row] Full Result Object: {json.dumps(result['result'], indent=2)}"
            )

        logger.debug(f"[Row] Full MCP Response: {json.dumps(result, indent=2)}")

        # Check for errors in MCP response (multiple patterns)
        # Pattern 1: Explicit error key
        if "error" in result:
            raise MCPCommunicationError(
                "transform_rows", result.get("error", "Unknown MCP error"), result
            )

        # Pattern 2: success=False at root level
        if result.get("success") is False:
            error_msg = result.get("message", "MCP operation failed")
            raise MCPCommunicationError("transform_rows", error_msg, result)

        # Pattern 3: success=False inside result object
        if "result" in result and isinstance(result["result"], dict):
            if result["result"].get("success") is False:
                error_msg = result["result"].get("message", "MCP operation failed")
                raise MCPCommunicationError("transform_rows", error_msg, result)

        # Extract download URL from result - try multiple paths
        download_url = None
        resolved_file_id = None

        # Path 1: Direct download_url
        if "download_url" in result:
            download_url = result["download_url"]
            logger.debug("[Row] Found download_url at root level")
        if "file_id" in result and isinstance(result["file_id"], str):
            resolved_file_id = result["file_id"]

        # Path 2: result.result_file_url or result.download_url
        elif "result" in result and isinstance(result["result"], dict):
            result_obj = result["result"]

            # Try result_file_url first
            if "result_file_url" in result_obj:
                download_url = result_obj["result_file_url"]
                logger.debug("[Row] Found download_url at result.result_file_url")

            # Try download_url in result
            elif "download_url" in result_obj:
                download_url = result_obj["download_url"]
                logger.debug("[Row] Found download_url at result.download_url")

            if "file_id" in result_obj and isinstance(result_obj["file_id"], str):
                resolved_file_id = result_obj["file_id"]

            # Try odata.result_file_url
            elif "odata" in result_obj and isinstance(result_obj["odata"], dict):
                download_url = result_obj["odata"].get("result_file_url")
                if download_url:
                    logger.debug(
                        "[Row] Found download_url at result.odata.result_file_url"
                    )

        # Path 3: output_file_url (common in some MCP responses)
        if not download_url and "output_file_url" in result:
            download_url = result["output_file_url"]
            logger.debug("[Row] Found download_url as output_file_url")

        # Path 4: result_file_url at root
        if not download_url and "result_file_url" in result:
            download_url = result["result_file_url"]
            logger.debug("[Row] Found download_url as result_file_url")

        if not download_url and resolved_file_id:
            download_url = settings_provider.get_file_download_url(resolved_file_id)
            logger.debug("[Row] Built download_url from file_id fallback")

        if not download_url:
            raise TransformationError(
                "extract_result",
                "No download URL found in MCP response",
                {
                    "mcp_response": result,
                    "checked_paths": [
                        "download_url",
                        "result.result_file_url",
                        "result.download_url",
                        "result.odata.result_file_url",
                        "output_file_url",
                        "result_file_url",
                    ],
                },
            )

        final_file_id = resolved_file_id
        if not final_file_id and "/download/" in download_url:
            final_file_id = download_url.split("/download/")[-1]

        return {
            "message": result.get("message"),
            "download_url": download_url,
            "file_id": final_file_id,
            "raw_result": result,  # For debugging
        }

    except (MCPCommunicationError, TransformationError):
        raise  # Re-raise domain exceptions as-is
    except Exception as e:
        logger.error(f"[transform_row] Unexpected error: {e}", exc_info=True)
        raise TransformationError(
            "mcp_call",
            f"MCP transformation failed: {str(e)}",
            {"payload": payload if "payload" in locals() else None},
        ) from e
