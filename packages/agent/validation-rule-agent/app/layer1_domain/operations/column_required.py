OPERATION = {
  "scope": "column",
  "type": "required",
  "description": (
    "Ensure column is not null or empty. Use this for requests like 'not empty', "
    "'non-empty', 'required', 'must not be blank', or 'must have a value'."
  ),
  "example": {
    "rule_name": "Required Name",
    "type": "required",
    "params": {
      "columns": ["name"]
    },
    "error_message": "Name is required"
  }
}