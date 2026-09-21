import json
import logging

from agent_sdk import OpenAIService
from app.layer1_domain.entities.validation_rule_agent_data import ValidationRuleAgentData

logging.basicConfig(level=logging.INFO)

async def generate_polar_column(
    long_input: str,
    header: dict | None = None
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

        Batching:
        - Supports multiple operations in a single call


        Returns:
        {
            "message": str
        }
        """

    if not long_input:
        return {"error": "Missing long_input"}

    llm = OpenAIService()
    schema_json = json.dumps(header or {}, indent=2)

    system_prompt = """
        You are a data transformation assistant.

        Your task is to convert user requests into a VALID JSON payload for a COLUMN transformation API.
        Dataset schema:
        {DATASET_SCHEMA}
        ========================
        OUTPUT FORMAT (STRICT)
        ========================
        - Output ONLY valid JSON
        - NO explanation
        - NO markdown
        - NO extra text
        - For every rule that requires an expression, the expression field MUST be present as a non-empty string
        - Expression fields can NEVER be null, empty, whitespace-only, or omitted
        - Expression fields can NEVER contain serialized JSON, arrays, or objects
        - Resolve column names from the dataset schema.

        Schema:
        {
        "rules": [
            {
            "scope": "column",
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
        POLARS EXPRESSION RULES (CRITICAL)
        ========================

        Treat expressions as executable Polars code, not natural language.

        You MUST generate ONLY valid Polars expressions.
        Every required expression must be a non-empty string.
        Never output null, "", " ", [], {{}}, or missing for any expression field.
        Never place a full rule object or JSON array inside params.expression.

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

        REQUIRED:
        - params.expression MUST exist
        - params.expression MUST be a non-empty Polars boolean expression string
        - params.expression MUST NEVER be null, empty, whitespace-only, or JSON

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

        REQUIRED:
        - params.expression MUST exist
        - params.expression MUST be a non-empty Polars expression string
        - params.expression MUST NEVER be null, empty, whitespace-only, or JSON
        - If you cannot build a valid expression string, return an error JSON instead of emitting the rule

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
        - Every conditions[].when_expression MUST be a non-empty Polars condition string
        - conditions[].when_expression must NEVER be null, empty, whitespace-only, or JSON
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
        - Validate that every required expression field is present and non-empty
        - Reject any rule where params.expression or conditions[].when_expression is null, empty, whitespace-only, missing, or JSON
        - Max 100 rules per request

        If invalid:
        Return:
        {
        "rules": [],
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

    verification_prompt = """
        You are a JSON payload checker.

        Read the payload step by step in order.
        Simulate each rule as if it has already been applied before checking the next rule.

        Fix sequential dependency mistakes only:
        - Track the effective value of each column after every rule.
        - If an earlier rule changes a column value, treat that column as updated for all later rules.
        - Any later rule that reads that same column must use the latest post-update value, not the pre-update value.
        - Rewrite later conditions that still use the old value.
        - Reject any filter or mapping rule whose params.expression is null, empty, whitespace-only, missing, or not a string.
        - Reject any filter or mapping rule whose params.expression contains serialized JSON instead of a Polars expression.
        - Reject any case_when_expr condition whose when_expression is null, empty, whitespace-only, missing, or not a string.
        - If an invalid expression can be repaired safely, replace it with a valid non-empty Polars expression string.
        - If it cannot be repaired safely, return an error JSON instead of keeping the invalid rule.

        Use the rule sequence and the updated column state to decide each later condition.
        Do not copy earlier trigger values into dependent steps.

        Only update the minimum needed parts of the payload.
        Keep the same rule order, rule names, and unchanged expressions.

        Return only the corrected JSON.
        """

    try:
        response = await llm.get_chat_completion(system_prompt, user_prompt)
        logging.info(f"[Polar_Column] LLM response: {response}")
        response = await llm.get_chat_completion(verification_prompt, response)
        logging.info(f"[Polar_Column] LLM verification response: {response}")
        payload = json.loads(response)
        rules = payload.get("rules")
        error = payload.get("error")
        if rules is None or not rules:
            return ValidationRuleAgentData.invalid(expression=[], message=error).to_dict()

    except Exception as e:
        return {
            "error": "Failed to parse LLM output",
            "details": str(e),
            "raw_response": response if "response" in locals() else None
        }

    try:
        logging.info(f"[Polar_Column] Generating API with payload: {json.dumps(payload)}")
        return ValidationRuleAgentData.success(payload.get("rules", [])).to_dict()

    except Exception as e:
        return ValidationRuleAgentData.invalid(
            expression=payload.get("rules", [])
        ).to_dict()