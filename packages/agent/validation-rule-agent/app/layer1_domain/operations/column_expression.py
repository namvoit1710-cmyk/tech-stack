
OPERATION = {
  "scope": "column",
  "type": "expression",
  "description": (
    "Custom validation using a Polars expression. The expression must return TRUE for valid rows "
    "and FALSE for invalid rows. Use this for format, range, pattern, or other custom logic checks. "
    "Examples include valid email format, numeric thresholds, and string patterns. "
    "Use Polars syntax only and use pl.col() to reference columns. "
    "Do not use Python built-ins or plain Python string logic such as len(), str(), re, lambda, "
    "list comprehensions, or and/or/not. Use Polars operators and namespaces like &, |, ~, "
    ".str.contains(), .str.len_chars(), .is_null(), .is_not_null(), and comparison operators instead. "
    "For example, phone validation should be written as "
    "pl.col('phone').str.contains(r'^\\d+$') & (pl.col('phone').str.len_chars() >= 10). "
    "Always use single quotes ('column_name') inside pl.col(), and when using cast(), "
    "you must include strict=False."
  ),
  "example": {
    "rule_name": "Valid Email Format",
    "type": "expression",
    "params": {
      "columns": ["email"],
      "expression": "pl.col('email').str.contains(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')"
    },
    "error_message": "Invalid email format"
  }
}