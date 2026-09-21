OPERATION = {
  "scope": "column",
  "type": "unique",
  "description": (
    "Ensure column values are unique. Use this for requests like 'unique', "
    "'no duplicates', 'must not repeat', or 'distinct values only'."
  ),
  "example": {
    "rule_name": "Unique ID",
    "type": "unique",
    "params": {
      "columns": ["id"]
    },
    "error_message": "Duplicate IDs found"
  }
}