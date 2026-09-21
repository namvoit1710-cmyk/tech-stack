import json
from agent_sdk import tool, OpenAIService
from .get_first_n_rows import _get_first_n_rows_impl
from app.layer4_frameworks.data_factory.transformation_client import DataFactoryClient
import logging
logging.basicConfig(level=logging.INFO)

# API_URL = "https://smdg-ai-dev-data-factory-qas.cfapps.br10.hana.ondemand.com/api/v1/transformation"

@tool("transform_column", internal=True)
async def transform_column(
    long_input: str = None,
    file_id: str = None
) -> dict:
    """
        Transform columns in a file using natural language instructions.

        Capabilities:
        - Filter columns using conditions
        - Create or update columns using expressions
        - Drop columns
        - Rename columns
        - Assign values using conditional logic

        Input:
        - long_input: natural language instruction
        - file_id: input file identifier

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
        """

    if not long_input:
        return {"error": "Missing long_input"}
    if not file_id:
        return {"error": "Missing file_id"}

    preview_rows = _get_first_n_rows_impl(file_id, 10)

    llm = OpenAIService()

    system_prompt = """
        You are a data transformation assistant.

        Your task is to convert user requests into a VALID JSON payload for a COLUMN transformation API.

        ========================
        OUTPUT FORMAT (STRICT)
        ========================
        - Output ONLY valid JSON
        - NO explanation
        - NO markdown
        - NO extra text

        Schema:
        {
        "file_path": "<string>",
        "file_format": "csv",
        "rules": [
            {
            "rule_name": "<string>",
            "type": "filter" | "mapping" | "drop_columns" | "rename_columns" | "case_when_expr",
            "params": { ... }
            }
        ]
        }

        ========================
        SUPPORTED RULE TYPES
        ========================
        Only:
        - filter
        - mapping
        - drop_columns
        - rename_columns
        - case_when_expr

        Reject or convert anything else.

        ========================
        HEADER DETECTION RULE (IMPORTANT)
        ========================
        - Header is NOT guaranteed to be first row
        - Infer schema from sample rows
        - Column names are CASE-SENSITIVE
        - DO NOT modify column names
        - DO NOT normalize naming

        Once inferred:
        - Use ONLY those column names exactly as-is

        ========================
        POLARS EXPRESSION RULES (CRITICAL)
        ========================

        Treat expressions as executable Polars code, not natural language.

        You MUST generate ONLY valid Polars expressions.

        1. ONLY use Polars syntax
        - Use pl.col("column_name") to reference columns

        2. DO NOT use Python built-ins or syntax:
        - NO len()
        - NO str()
        - NO re (regex module)
        - NO lambda
        - NO list comprehensions
        - NO Python logical operators: and, or, not

        3. Use ONLY Polars operators:
        - AND → &
        - OR  → |
        - NOT → ~

        4. Use ONLY Polars methods:
        - .str.contains()
        - .str.len_chars()
        - .is_null()
        - .is_not_null()
        - .cast()
        - .fill_null()

        5. All column references MUST use:
        pl.col("column_name")

        6. String operations MUST use Polars string namespace:
        pl.col("name").str.contains("abc")

        7. Null checks MUST use:
        pl.col("x").is_null()
        pl.col("x").is_not_null()

        8. Conditions must ALWAYS be valid Polars expressions:
        Example: pl.col("age") > 18
        NOT: age > 18

        9. For row-position-based updates, use ONLY:
        - pl.arange(0, pl.len()) == <zero_based_row_index>
        - Combine multiple row positions with |
        - Do NOT use .over([]), row_number(), row_nr, helper columns, or placeholder expressions

        10. For literal replacement values:
        - Wrap every literal replacement with pl.lit(...)
        - Bare strings, numbers, and booleans must not be used as replacement values unless they are explicitly column references via pl.col(...)

        If any rule is violated:
        - Do NOT guess
        - Return an error JSON instead

        ========================
        COLUMN RULES (STRICT)
        ========================
        - ONLY use columns from inferred header
        - DO NOT invent columns
        - DO NOT modify casing
        - DO NOT translate column names
        - Users may provide fuzzy or partial column names; map them to the closest matching header column if confident

        If unsure:
        - try closest match
        - otherwise return error

        ========================
        ROW-SPECIFIC VALUE UPDATES
        ========================
        - If the user wants to set, fill, replace, or update values in an existing column for only some rows, treat it as a conditional column update.
        - This includes simple literal substitutions in place, such as changing one code or label to another in the same column.
        - Emit exactly one MAPPING rule for that destination column.
        - When updating an existing column, set new_col to that same column name.
        - Use a Polars expression that preserves untouched rows with otherwise(pl.col("<same column>")) or the equivalent current-column reference.
        - For the replacement value in a same-column update, use pl.lit(<replacement_value>).
        - If multiple rows in the same column need the same value, combine them into one mapping rule instead of emitting separate rules.
        - If multiple rows in the same column need different literal values, still emit one MAPPING rule using a chained pl.when(...).then(...).when(...).then(...).otherwise(pl.col("<same column>")).
        - Treat user row numbers as 1-based data rows after the header.
        - Convert user row N to zero-based Polars row index N-1.
        - For row-number targeting, the condition must use pl.arange(0, pl.len()) == <zero_based_index>.        
        - Do NOT use case_when_expr for preservation-style updates.
        - Do NOT use case_when_expr when the user targets specific row numbers in an existing column.
        - Do NOT emit placeholders like "__KEEP__" or "KEEP" anywhere in the output.
        ========================
        RULE DEFINITIONS
        ========================

        1. FILTER
        Keeps columns where expression is TRUE

        params:
        {
        "expression": "<polars boolean expression>"
        }

        Example:
        pl.col("status") == "active"

        ---

        2. MAPPING
        Create or overwrite a column using a Polars expression.
        Use this rule for row-specific updates to an existing column when untouched rows must keep their current values.

        params:
        {
        "expression": "<polars expression>",
        "new_col": "<column name>"
        }

        Example:
        pl.col("price") * pl.col("qty")

                Row-specific update pattern:
                pl.when((pl.arange(0, pl.len()) == idx1) | (pl.arange(0, pl.len()) == idx2))
                    .then(pl.lit("value"))
                    .otherwise(pl.col("existing_column"))

        ---

        3. DROP_COLUMNS
        Remove columns from dataset

        params:
        {
        "columns_to_drop": ["col1", "col2"]
        }

        ---

        4. RENAME_COLUMNS
        Rename existing columns

        params:
        {
        "mapping": {
            "old_name": "new_name"
        }
        }

        ---

        5. CASE_WHEN_EXPR
        Assign LITERAL values based on conditions only when the fallback should also be a literal replacement for every non-matching row.

        IMPORTANT:
        - "result" is a literal value
        - "otherwise" is a literal value
        - NOT expressions
        - Never use this rule to preserve the existing value in untouched rows
        - Never use this rule for row-number-based updates to an existing column
        - Never emit placeholders like "__KEEP__" or "KEEP" in "otherwise" to mean preserve current data
        - If you need to preserve current data, use MAPPING with otherwise(pl.col("<same column>")) instead

        params:
        {
        "column_dest": "<column>",
        "conditions": [
            {
            "when_expression": "<polars condition>",
            "result": "<literal value>"
            }
        ],
        "otherwise": "<literal value>"
        }

        ========================
        VALIDATION RULES
        ========================
        Before output:
        - Validate all columns exist
        - Validate Polars syntax rules
        - Max 100 rules per request

        If invalid:
        Return:
        {
        "file_path": "<file_path>",
        "file_format": "csv",
        "rules": [],
        "error": "<reason>"
        }

        ========================
        FINAL RULE
        ========================
        Return ONLY JSON.
"""

    user_prompt = f"""
        User request:
        {long_input}

        File ID:
        {file_id}

        Sample rows:
        {json.dumps(preview_rows)}

        Remember:
        - file_path = file_id
        """

    verification_prompt = """
        You are a JSON payload checker.

        Read the payload step by step in order.
        Simulate each rule as if it has already been applied before checking the next rule.

        Fix sequential dependency mistakes only:
        - Track the effective value of each column after every rule.
        - If an earlier rule changes a column value, treat that column as updated for all later rules.
        - Any later rule that reads that same column must use the latest post-update value, not the pre-update value.
        - Rewrite later conditions that still use the old value.

        Use the rule sequence and the updated column state to decide each later condition.
        Do not copy earlier trigger values into dependent steps.

        Only update the minimum needed parts of the payload.
        Keep the same rule order, rule names, and unchanged expressions.

        Return only the corrected JSON.
        """

    import time
    try:
        t_llm_start = time.time()
        response = await llm.get_chat_completion(system_prompt, user_prompt)
        t_llm_first = time.time()
        logging.info(f"[Column] LLM response: {response}")
        response = await llm.get_chat_completion(verification_prompt, response)
        t_llm_second = time.time()
        logging.info(f"[Column] LLM verification response: {response}")
        payload = json.loads(response)

        # enforce file_id
        payload["file_path"] = file_id

        llm_first_duration = t_llm_first - t_llm_start
        llm_second_duration = t_llm_second - t_llm_first
        total_llm_duration = t_llm_second - t_llm_start
        logging.info(f"[Column] LLM first call duration: {llm_first_duration:.2f} seconds")
        logging.info(f"[Column] LLM second call duration: {llm_second_duration:.2f} seconds")
        logging.info(f"[Column] LLM total duration: {total_llm_duration:.2f} seconds")

    except Exception as e:
        return {
            "error": "Failed to parse LLM output",
            "details": str(e),
            "raw_response": response if "response" in locals() else None
        }

    data_factory_client = DataFactoryClient()
    return data_factory_client.execute_column_payload(payload)