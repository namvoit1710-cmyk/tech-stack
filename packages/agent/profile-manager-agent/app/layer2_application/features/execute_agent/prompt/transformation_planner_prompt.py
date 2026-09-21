import json

def build_plan_prompt(operations_prompt):
    return f"""You are a query planner.

Convert user request into OData query parameters.

Available resource:
{operations_prompt}

Return JSON:

{{
  "status": "ok",
  "execution_plan":[
    {{
      "scope":"...",
      "odata_query": {{
        "filter": "...",
        "orderby": null,
        "top": null,
        "skip": null,
        "select": null,
        "count": null
      }}
    }}
  ]
}}

Rules:
- If a parameter is not requested by the user, return null.
- Follow OData syntax strictly.
- Combine filters using AND/OR correctly.
- String values must be wrapped in quotes

If the request cannot be mapped:
- Try to build a JSON that use new attrribute
"""