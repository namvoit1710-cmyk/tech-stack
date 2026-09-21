OPERATION = {
  "scope": "user",
  "description": "Operations under this scope are currently supported for 'user' resource or related to user. All OData parameters ($filter, $orderby, $top, $skip, $select, $count) apply to this resource.",
  "operations": [
    {
      "type": "filter",
      "description": """
        Filter records using OData-compatible expressions:
        - eq -> equals
        - ne -> not equals
        - gt -> greater than
        - ge -> greater than or equal
        - lt -> less than
        - le -> less than or equal
        - contains -> substring match
        - startswith -> prefix match
        - endswith -> suffix match
        Use logical operators (and/or) to combine multiple conditions.
        String values must be wrapped in quotes. Example: name eq 'Alice' (NOT name eq Alice).
        Detect queried field from user message. If the field is not specified, suggest from the query value.
        Consider try with these supported attribute 'id, email, userName, givenName, familyName, displayName, active (true/false), locale, timezone, userType' first.
        If no supported attribute is specified by user, the filter will default to the 'userName' field.
        """,
      "example": {"user_message": "user must be requester1", "rule_name": "Filter by name", "type": "filter", "params": {"expression": "name eq 'requester1'"}}
    },
    {
      "type": "orderby",
      "description": "Sort results by one or more fields.",
      "example": {"rule_name": "Sort by name descending", "type": "orderby", "params": {"fields": ["name desc"]}}
    },
    {
      "type": "pagination",
      "description": "Control pagination using skip and top.",
      "example": {"rule_name": "Pagination", "type": "pagination", "params": {"skip": 0, "top": 10}}
    },
    {
      "type": "select",
      "description": "Select specific fields to return.",
      "example": {"rule_name": "Select fields", "type": "select", "params": {"fields": ["id", "name"]}}
    },
    {
      "type": "count",
      "description": "Include total count of filtered records.",
      "example": {"rule_name": "Include count", "type": "count", "params": {"enabled": True}}
    }
  ]
}
