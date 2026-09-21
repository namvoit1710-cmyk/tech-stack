import json

def build_operations_prompt(operations):

    text = ""

    for op in operations:

        text += f"""
Scope: {op['scope']}
Description: {op['description']}
Operations: {op['operations']}
"""

    return text