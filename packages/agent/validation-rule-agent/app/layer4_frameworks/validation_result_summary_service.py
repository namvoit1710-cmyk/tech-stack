from __future__ import annotations

import logging
from typing import cast

from app.layer2_application.features.execute_agent.prompt.validation_result_summary_prompt import VALIDATION_RESULT_SUMMARY_PROMPT


class ValidationResultSummaryService:
    """Service to generate human-friendly validation result summaries using LLM."""

    def __init__(self, open_ai_service):
        self.open_ai_service = open_ai_service
        logging.info("ValidationResultSummaryService initialized")

    async def generate_summary(self, result: dict[str, object]) -> str:
        """
        Generate a friendly summary message from validation results.

        Args:
            result: Validation result dictionary containing:
                - message: Original message
                - result: Contains total_rows, valid_rows, invalid_rows, odata with error details
                - rules: list of validation rules applied

        Returns:
            str: Human-friendly summary message
        """
        try:
            # Extract relevant data (narrow the dynamic data-factory payload once).
            result_data = cast("dict[str, object]", result.get("result") or {})
            total_rows = result_data.get("total_rows", 0)
            valid_rows = result_data.get("valid_rows", 0)
            invalid_rows = result_data.get("invalid_rows", 0)

            odata = cast("dict[str, object]", result_data.get("odata") or {})
            error_details = cast("list[dict[str, object]]", odata.get("data") or [])

            # Build context for LLM
            errors: list[dict[str, object]] = []
            for error in error_details:
                errors.append({
                    "rule_name": error.get("rule_name", ""),
                    "violate": error.get("violate", 0),
                    "error_message": error.get("error_message", ""),
                })
            context: dict[str, object] = {
                "total_rows": total_rows,
                "valid_rows": valid_rows,
                "invalid_rows": invalid_rows,
                "errors": errors,
            }

            # Create prompt for LLM
            user_prompt = f"""Validation Results:
- Total rows: {total_rows}
- Valid rows: {valid_rows}
- Invalid rows: {invalid_rows}

Errors:
{self._format_errors_with_columns(error_details)}

Generate the summary message."""

            logging.info("step=generate_summary_with_llm total_rows=%s invalid_rows=%s errors=%d",
                        total_rows, invalid_rows, len(error_details))

            # Call LLM to generate summary (json_mode=False for plain text)
            summary = await self.open_ai_service.get_chat_completion(
                system_prompt=VALIDATION_RESULT_SUMMARY_PROMPT,
                user_prompt=user_prompt,
                json_mode=False
            )

            # Clean up the response
            summary = summary.strip()

            logging.info("step=summary_generated length=%d", len(summary))

            return summary

        except Exception as e:
            logging.exception("step=generate_summary_failed error=%s", str(e))
            # Fallback to original message
            message = result.get("message")
            return message if isinstance(message, str) else "Validation completed."

    def _format_errors(self, errors: list[dict[str, object]]) -> str:
        """Format errors for LLM prompt."""
        if not errors:
            return "No errors found"

        formatted = []
        for error in errors:
            formatted.append(
                f"- {error.get('error_message', 'Unknown error')}: {error.get('violate', 0)} rows"
            )

        return "\n".join(formatted)

    def _format_errors_with_columns(self, error_details: list[dict[str, object]]) -> str:
        """Format errors with column information for LLM prompt."""
        if not error_details:
            return "No errors found"

        formatted = []
        for error in error_details:
            rule_name = error.get('rule_name', 'Unknown Rule')
            error_message = error.get('error_message', 'Unknown error')
            violate = error.get('violate', 0)

            # Extract column name from error_code
            error_code = cast("dict[str, object]", error.get('error_code') or {})
            columns: list[object] = []
            for key, col_list in error_code.items():
                if isinstance(col_list, list):
                    columns.extend(col_list)

            column_name = columns[0] if columns else 'Unknown'

            formatted.append(
                f"- Rule: {rule_name}\n  Error: {error_message}\n  Violations: {violate} rows\n  Column: {column_name}"
            )

        return "\n".join(formatted)
