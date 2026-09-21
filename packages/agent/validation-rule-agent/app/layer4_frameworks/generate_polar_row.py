import json
import logging

from agent_sdk import OpenAIService
from app.layer1_domain.entities.validation_rule_agent_data import ValidationRuleAgentData

logging.basicConfig(level=logging.INFO)

async def generate_polar_row(
    long_input: str,
    header: dict | None = None,
) -> dict:
    """
        Transform rows in a file using natural language instructions.

        Capabilities:
        - Delete rows (by index, condition, or exact values)
        - Insert rows at the start of the CSV file or at a specific index

        Input:
        - long_input: natural language instruction
        - file_id: input file identifier

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
        - Resolve column names from the dataset schema. 

        Returns:
        {
            "file_id": str,
            "download_url": str,
            "message": str
        }
        """

    if not long_input:
        return {"error": "Missing long_input"}

    llm = OpenAIService()
    schema_json = json.dumps(header or {}, indent=2)

    system_prompt = """
        You are a data transformation assistant.

        Your task is to convert user requests into a VALID JSON payload for a row transformation API.
        Dataset schema:
        {DATASET_SCHEMA}

        ========================
        OUTPUT FORMAT (STRICT)
        ========================
        - Output ONLY valid JSON
        - NO explanation
        - NO markdown
        - NO extra text

        Schema:
        {
        "rules": [
            {
            "scope": "row",
            "operation": "insert" | "delete",
            "row_identifier": {
                "type": "index" | "condition" | "values",
                "index": <int>,
                "expression": "<polars expression>",
                "match_values": { "column": "value" }
            },
            "position": "before" | "after" | "at_index",
            "data": { "column": "value" }
            }
        ]
        }

        ========================
        SUPPORTED OPERATIONS
        ========================
        Only:
        - insert
        - delete

        Reject or convert anything else.

        ========================
        ROW IDENTIFIER RULES (STRICT & PRECISE)
        ========================

        - Use EXACTLY ONE row_identifier when required
        - It MUST match EXACTLY ONE row (no 0, no multiple matches)

        ------------------------
        1. type = "index"
        ------------------------
        {
        "type": "index",
        "index": <int>
        }

        - Use ONLY if user provides row number
        - User-facing row numbers are 1-based and count only data rows after the header
        - The API "index" field is 0-based, so row 1 maps to index 0, row 2 maps to index 1, and so on
        - Do NOT emit index 1 for the first data row
        - DO NOT guess index
        - If fallback needed → use index = 0 with "at_index" ONLY

        ------------------------
        2. type = "values"
        ------------------------
        {
        "type": "values",
        "match_values": { "column": "value" }
        }

        - Use exact matches only
        - MUST uniquely identify one row
        - Use multiple columns if needed

        ------------------------
        3. type = "condition"
        ------------------------
        {
        "type": "condition",
        "expression": "<polars expression>"
        }

        - MUST match exactly one row
        - Use == and combine with `&` if needed
        - Avoid broad conditions (>, <, etc.)

        ========================
        HEADER DETECTION RULE (IMPORTANT)
        ========================
        You will be given sample rows from the file.

        - The header is NOT guaranteed to be the first row
        - You must infer the header by analyzing structure
        - Header is usually:
        • row with column-like names (strings, not data-like values)
        • most stable schema-like row
        • consistent across dataset

        Once header is identified:
        - Use it as the ONLY source of truth for column names
        - Column names are CASE-SENSITIVE and MUST NOT be modified

        Examples:
        - "Id" ≠ "id" ≠ "ID"
        - Must use EXACT match from inferred header

        If unsure about header:
        - choose the most schema-like row
        - prioritize consistency over guessing

        TYPE HANDLING (IMPORTANT)
        - Infer column value types from sample rows
        - CSV values are often strings

        - When building conditions:
        • Match the actual data format in the file
        • If values look like "77" → use string comparison

        Example:
        pl.col("age") == "77"   correct if "age" column values are strings
        pl.col("age") == 77     wrong if "age" column values are strings
        ========================
        COLUMN RULES (STRICT)
        ========================
        - ONLY use columns from inferred header
        - DO NOT modify casing
        - DO NOT normalize names
        - DO NOT invent columns
        - Users may provide fuzzy or partial column names; map them to the closest matching header column if confident

        If user mentions invalid column:
        - try to map to closest header match
        - if uncertain → return error

        ========================
        INSERT RULES
        ========================
        - "data" is required
        - Missing values → fill with "UNKNOWN" only if column exists in header
        - If user does not specify position when inserting → Strictly insert at the top of the CSV file using position "at_index" and row_identifier index 0
  

        ========================
        DELETE RULES
        ========================
        - requires row_identifier only

        ========================
        VALIDATION
        ========================
        Before output:
        - verify all columns exist in inferred header
        - verify correct casing
        - max 100 operations

        If invalid:
        {
        "file_path": "<file_path>",
        "file_format": "csv",
        "operations": [],
        "error": "<reason>"
        }

        ========================
        FINAL RULE
        ========================
        Return ONLY JSON.
    """.replace("{DATASET_SCHEMA}", schema_json)

    user_prompt = f"""
        User request:
        {long_input}
        """

    

    try:
        response = await llm.get_chat_completion(system_prompt, user_prompt)
        logging.info(f"[Polar_Column] LLM response: {response}")
        payload = json.loads(response)
        rules = payload.get("rules")
        if rules is None or not rules:
            return ValidationRuleAgentData.invalid(expression=[]).to_dict()

    except Exception as e:
        return {
            "error": "Failed to parse LLM output",
            "details": str(e),
            "raw_response": response if "response" in locals() else None
        }

    try:
        logging.info(f"[Polar_Row] Generating API with payload: {json.dumps(payload)}")
        return ValidationRuleAgentData.success(payload.get("rules", [])).to_dict()

    except Exception as e:
        return ValidationRuleAgentData.invalid(
            expression=payload.get("rules", [])
        ).to_dict()