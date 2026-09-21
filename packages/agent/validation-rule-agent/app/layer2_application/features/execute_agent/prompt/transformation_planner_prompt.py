import json

def build_plan_prompt(header, operations_prompt):
    return f"""You are a data validation planner.

Convert user request into validation rules.

Dataset schema:
{json.dumps(header, indent=2)}

Once header is identified:
  - Use it as the ONLY source of truth for column names
  - Column names are CASE-SENSITIVE and MUST NOT be modified

Examples:
  - "Id" ≠ "id" ≠ "ID"
  - Must use EXACT match from inferred header

If no header is available:
  - Infer the rule directly from the user message
  - Create the best possible validation rule from the user request
  - Do not block rule creation just because column names cannot be confirmed from a header

Available operations:
{operations_prompt}

Planning rules:
- Map common natural-language requests to the closest available operation.
- Treat phrases like 'not empty', 'non-empty', 'must not be blank', 'is required', and 'must have a value' as the `required` operation.
- Treat phrases like 'unique', 'no duplicates', and 'must not repeat' as the `unique` operation.
- Use the `expression` operation for format, range, pattern, or custom logical checks.
- Treat phrases like 'no dangerous things', 'no hazardous/prohibited/banned/blacklisted substances', 'not allowed in this data', or 'must not be in the blocklist' as the `reference_lookup` operation against the dangerous-substance key-set (`reference_id` 'dangerously_validate', `violate_when` 'in_set'). Apply it to the column holding the substance/compound/material name (e.g. `Material`). Use `violate_when` 'not_in_set' for allow-list style 'must be one of the reference values' checks. Prefer `reference_lookup` over `expression` whenever the check is membership against a known/external list rather than an in-row pattern.
- Resolve column names from the dataset schema. If the user says 'column name not empty', interpret `name` as the column name because it exists in the schema.
- Prefer a supported operation whenever the request can be reasonably mapped to one.
- Return `not_supported` only when the request truly cannot be expressed using the available operations.

Examples:
- 'column name not empty' -> a `required` rule on column `name`
- 'id must be unique' -> a `unique` rule on column `id`
- 'email must be a valid email address' -> an `expression` rule on column `email`
- 'validate file by rules: no dangerous things allowed in this data' -> a `reference_lookup` rule on the substance/compound column (e.g. `Material`) with params reference_id 'dangerously_validate', violate_when 'in_set'

Return JSON:

{{
  "status": "ok",
  "execution_plan":[
    {{
      "scope":"column",
      "rule_name":"..."
      "type":"...",
      "params": {{}},
      "error_message": "..."
    }}
  ]
}}

- If the user request CANNOT be handled by any available operations, return a JSON object like this:

{{
  "status": "not_supported",
  "message": "The request cannot be handled by available operations."
}}
"""