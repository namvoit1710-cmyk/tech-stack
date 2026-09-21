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
# The wrapper in tools/transform_column.py handles tool registration
async def transform_column(
    long_input: str | None = None,
    file_id: str | None = None,
    data_factory_gateway: Optional[IDataFactoryGateway] = None,
    structured_llm_gateway: Optional[IStructuredLLMGateway] = None,
    file_preview_gateway: Optional[IFilePreviewGateway] = None,
    settings_provider: Optional[IRuntimeSettingsProvider] = None,
) -> dict:
    """
    Transform columns in a file using natural language instructions.

    Capabilities:
    - Filter columns using conditions
    - Create = Insert or update columns using expressions
    - Drop columns
    - Rename columns
    - Assign values using conditional logic

    Input:
    - long_input: natural language instruction (required)
    - file_id: input file identifier (required)
    - structured_llm_gateway: Optional structured LLM gateway instance (for dependency injection)

    Batching:
    - Supports multiple operations in a single call

    Output behavior (IMPORTANT):
    - Always produces a NEW file
    - Returns a new file_id representing the latest version
    - This file_id must be used for subsequent steps if chaining operations

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
            "Structured LLM gateway is required for column transformation"
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
You are a data transformation assistant. Convert user requests into VALID JSON for COLUMN transformation API.

OUTPUT: ONLY valid JSON. NO explanation, markdown, or extra text.

SCHEMA:
{
  "file_path": "<string>",
  "file_format": "csv",
  "rules": [{
    "rule_name": "<string>",
    "type": "filter" | "mapping" | "drop_columns" | "rename_columns" | "case_when_expr",
    "params": {...}
  }]
}

RULE TYPES: filter, mapping, drop_columns (=delete_columns), rename_columns, case_when_expr. Max 100 rules.

HEADER: May NOT be first row. Infer from sample. Column names are CASE-SENSITIVE and immutable.

POLARS EXPRESSION RULES (CRITICAL - executable code, not natural language):
1. ONLY Polars syntax: pl.col("name")
2. NO Python: len(), str(), re, lambda, comprehensions, and/or/not
3. Operators: & (AND), | (OR), ~ (NOT)
4. Methods: .str.contains(), .str.len_chars(), .is_null(), .is_not_null(), .cast(), .fill_null()
5. Strings: pl.col("name").str.contains("abc")
6. Nulls: pl.col("x").is_null()
7. Conditions: pl.col("age") > 18 (NOT: age > 18)
8. Row position: pl.arange(0, pl.len()) == <idx>. Combine with |. NO .over([]), row_number(), row_nr
9. Literals: Wrap with pl.lit(...). NO bare strings/numbers unless pl.col(...)

COLUMNS: ONLY from inferred header. Map fuzzy input to closest match. NO inventing, casing changes, or translation.

ROW-SPECIFIC UPDATES (in existing columns):
- Use MAPPING rule (NOT case_when_expr for preservation)
- Set new_col = same column name
- Preserve untouched rows: .otherwise(pl.col("<same_column>"))
- Replacement values: pl.lit(<value>)
- Multiple rows, same value: one rule with combined conditions
- Different values per row: one chained pl.when().then().when().then().otherwise()
- User rows 1-based → convert to 0-based: row N = index N-1
- Row targeting: pl.arange(0, pl.len()) == <idx>
- NO placeholders like "__KEEP__" or "KEEP"

RULE DEFINITIONS:

1. FILTER - Keep rows where TRUE
   params: {"expression": "<polars boolean>"}
   Ex: pl.col("status") == "active"

2. MAPPING - Create/overwrite column
   params: {"expression": "<polars expr>", "new_col": "<name>"}
   Ex: pl.col("price") * pl.col("qty")
   Row update pattern: pl.when((pl.arange(0, pl.len()) == idx1) | ...).then(pl.lit("val")).otherwise(pl.col("existing"))

3. DROP_COLUMNS - Remove columns
   params: {"columns_to_drop": ["col1", "col2"]}

4. RENAME_COLUMNS - Rename columns
   params: {"mapping": {"old": "new"}}

5. CASE_WHEN_EXPR - Assign LITERAL values (all rows get literal, not preservation)
   params: {"column_dest": "<col>", "conditions": [{"when_expression": "<polars>", "result": "<literal>"}], "otherwise": "<literal>"}
   NEVER use for: preservation, row-number updates to existing column, placeholders

VALIDATION: Verify columns exist, Polars syntax correct, max 100 rules.
If invalid: {"file_path": "<path>", "file_format": "csv", "rules": [], "error": "<reason>"}

FINAL: Return ONLY JSON.
"""

    user_prompt = f"""
User request: {long_input}
File ID: {file_id}
Sample rows: {json.dumps(preview_rows)}

Set file_path = file_id in output.
"""

    verification_prompt = """
JSON payload checker. Simulate rules sequentially and fix dependency mistakes:
- Track column values after each rule
- If rule N changes column X, all later rules reading X must use post-update value
- Rewrite conditions using old values to use updated values
- Keep same rule order, names, unchanged expressions
Return only corrected JSON.
"""

    try:
        payload = await structured_llm_gateway.generate_column_transform_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            verification_prompt=verification_prompt,
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

    # Call Data Factory via MCP protocol
    try:
        logger.info(f"[Column] Calling MCP with payload: {json.dumps(payload)}")

        # Call MCP tool (returns dict)
        result = await data_factory_gateway.transform_file(
            file_path=payload["file_path"],
            rules=payload["rules"],
            file_format=payload.get("file_format", "csv"),
            output_format=payload.get("output_format", "csv"),
        )

        result = _normalize_mcp_result(result)

        # Log MCP response for debugging
        logger.info(f"[Column] MCP Response Keys: {list(result.keys())}")
        logger.info(f"[Column] MCP Success: {result.get('success')}")
        logger.info(f"[Column] MCP Message: {result.get('message')}")

        # Log result object if exists
        if "result" in result and isinstance(result["result"], dict):
            logger.info(f"[Column] Result Keys: {list(result['result'].keys())}")
            logger.debug(
                f"[Column] Full Result Object: {json.dumps(result['result'], indent=2)}"
            )

        logger.debug(f"[Column] Full MCP Response: {json.dumps(result, indent=2)}")

        # Check for errors in MCP response (multiple patterns)
        # Pattern 1: Explicit error key
        if "error" in result:
            raise MCPCommunicationError(
                "transform_file", result.get("error", "Unknown MCP error"), result
            )

        # Pattern 2: success=False at root level
        if result.get("success") is False:
            error_msg = result.get("message", "MCP operation failed")
            raise MCPCommunicationError("transform_file", error_msg, result)

        # Pattern 3: success=False inside result object
        if "result" in result and isinstance(result["result"], dict):
            if result["result"].get("success") is False:
                error_msg = result["result"].get("message", "MCP operation failed")
                raise MCPCommunicationError("transform_file", error_msg, result)

        # Extract download URL from result - try multiple paths
        download_url = None
        resolved_file_id = None

        # Path 1: Direct download_url at root
        if "download_url" in result:
            download_url = result["download_url"]
            logger.debug("[Column] Found download_url at root level")
        if "file_id" in result and isinstance(result["file_id"], str):
            resolved_file_id = result["file_id"]

        # Path 2: result.result_file_url or result.download_url
        elif "result" in result and isinstance(result["result"], dict):
            result_obj = result["result"]

            # Try result_file_url first
            if "result_file_url" in result_obj:
                download_url = result_obj["result_file_url"]
                logger.debug("[Column] Found download_url at result.result_file_url")

            # Try download_url in result
            elif "download_url" in result_obj:
                download_url = result_obj["download_url"]
                logger.debug("[Column] Found download_url at result.download_url")

            # Some MCP variants return file_id only
            if "file_id" in result_obj and isinstance(result_obj["file_id"], str):
                resolved_file_id = result_obj["file_id"]

            # Try odata.result_file_url
            elif "odata" in result_obj and isinstance(result_obj["odata"], dict):
                download_url = result_obj["odata"].get("result_file_url")
                if download_url:
                    logger.debug(
                        "[Column] Found download_url at result.odata.result_file_url"
                    )

        # Path 3: output_file_url (common in some MCP responses)
        if not download_url and "output_file_url" in result:
            download_url = result["output_file_url"]
            logger.debug("[Column] Found download_url as output_file_url")

        # Path 4: result_file_url at root
        if not download_url and "result_file_url" in result:
            download_url = result["result_file_url"]
            logger.debug("[Column] Found download_url as result_file_url")

        # Fallback: build download_url from file_id when URL is not returned
        if not download_url and resolved_file_id:
            download_url = settings_provider.get_file_download_url(resolved_file_id)
            logger.debug("[Column] Built download_url from file_id fallback")

        if not download_url:
            raise TransformationError(
                "extract_result",
                "No download URL found in MCP response",
                {
                    "mcp_response": result,
                    "checked_paths": [
                        "download_url",
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
            "raw_result": result,
        }

    except (MCPCommunicationError, TransformationError):
        raise  # Re-raise domain exceptions as-is
    except Exception as e:
        logger.error(f"[transform_column] Unexpected error: {e}", exc_info=True)
        raise TransformationError(
            "mcp_call",
            f"MCP transformation failed: {str(e)}",
            {"payload": payload if "payload" in locals() else None},
        ) from e
