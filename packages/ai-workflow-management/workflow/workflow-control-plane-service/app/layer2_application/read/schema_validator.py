"""Runtime validation of workflow input data against auto-form schema."""

from typing import Any


class SchemaValidator:
    @staticmethod
    def validate_input(input_data: dict[str, Any], schema: list[dict[str, Any]]) -> list[str]:
        """Validate input_data against a field-list schema.

        Only checks required-field presence.  Returns a list of error strings
        (empty means valid).
        """
        errors: list[str] = []
        for field_def in schema:
            key = field_def.get("key", "")
            rules = field_def.get("rules")
            if isinstance(rules, dict) and rules.get("isRequired") and key not in input_data:
                label = field_def.get("fieldConfig", {}).get("label", key)
                errors.append(f"Required field '{label}' ({key}) is missing")
        return errors
