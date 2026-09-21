import json

def build_operations_prompt(operations):

    text = ""

    for op in operations:

        text += f"""
Operation Type: {op['type']}
Scope: {op['scope']}
Description: {op['description']}

Example:
{json.dumps(op['example'], indent=2)}
"""

    return text