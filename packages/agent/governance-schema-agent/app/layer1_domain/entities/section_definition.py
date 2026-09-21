from dataclasses import dataclass
from typing import List, Dict, Optional
import json
import httpx
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


# =========================================================
# 1. BASE MODEL
# =========================================================

@dataclass(frozen=True)
class SectionDefinition:
    section: str
    fields: List[str]


# =========================================================
# 2. REGISTRY (SOURCE OF TRUTH)
# =========================================================

class SectionDefinitions:
    @staticmethod
    def normalize(api_data: list[dict]) -> list[SectionDefinition]:
        # if not api_data or not isinstance(api_data, list):
        #     raise ValueError("API data must be a non-empty list")

        section_definitions: list[SectionDefinition] = []
        for item in api_data:
            section_name = item.get("section")
            if not section_name:
                continue

            fields = item.get("fields", [])
            if not isinstance(fields, list):
                fields = []

            section_definitions.append(
                SectionDefinition(
                    section=section_name,
                    fields=[str(field) for field in fields],
                )
            )

        # if not section_definitions:
        #     raise ValueError("No valid section definitions found in API data")

        return section_definitions


async def fetch_section_definitions(template_id: str) -> list[SectionDefinition]:
    if not template_id:
        raise ValueError("template_id is required to fetch section definitions")

    settings = AppSettingsAdapter()
    url = f"{settings.GOVERNANCE_URL}/api/v1/templates/{template_id}/sections-with-fields"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    payload = response.json()
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        raise ValueError("sections-with-fields response must contain a list in 'data'")

    return SectionDefinitions.normalize(data)


# =========================================================
# 3. VALIDATION LOGIC
# =========================================================

def validate_parts(parts_str: str, allowed_sections: list[SectionDefinition]) -> str:
    """
    Validates that:
    - List is valid
    - section names exist in registry
    - fields are allowed for that section
    """

    try:
        parts_list = json.loads(parts_str)
    except Exception as e:
        raise ValueError(f"parts must be valid List string. Error: {e}")


    if not allowed_sections:
        raise ValueError("allowed section definitions are required to validate parts")

    result_parts = []
    for parts in parts_list:

        section_name = parts.get("section", "")
        section_data = parts.get("fields", "")

        section_def = next((s for s in allowed_sections if s.section == section_name), None)
        if not section_def:
            raise ValueError(f"Invalid section: {section_name}")

        # allowed_fields = set(section_def.fields)
        
        parts["fields"] = section_def.fields

        # if not isinstance(section_data, list):
        #     raise ValueError(f"'fields' must be a list in section {section_name}")

        # for field in section_data:
        #     if allowed_fields and field not in allowed_fields:
        #         raise ValueError(
        #             f"Invalid field '{field}' in section '{section_name}'. "
        #             f"Allowed: {sorted(list(allowed_fields))}"
        #         )
            
        result_parts.append(parts)
    return json.dumps(result_parts)


# =========================================================
# 4. OPTIONAL: HELPERS (useful for prompt or debugging)
# =========================================================

def format_section_definitions(section_definitions: Optional[list[SectionDefinition]] = None) -> str:
    """
    Useful for LLM prompt injection or debugging
    """
    if not section_definitions:
        return ""

    return "\n".join(
        f"- section={section_definition.section}"
        for section_definition in section_definitions
    )