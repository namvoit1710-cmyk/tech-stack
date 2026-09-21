"""Local tool: suggest capability slugs for a described business agent."""

from app.layer2_application.interfaces.register_tool import tool

_HINTS = {
    "email": "draft_email",
    "outreach": "outreach",
    "faq": "answer_faq",
    "support": "customer_support",
    "customer": "customer_support",
    "summar": "summarize",
    "report": "generate_report",
    "data": "analyze_data",
    "csv": "process_csv",
    "research": "research",
    "content": "create_content",
    "schedule": "schedule",
}


@tool
def suggest_capabilities(description: str) -> list[str]:
    """Return capability slugs inferred from a business-agent description."""
    low = (description or "").lower()
    slugs: list[str] = []
    for needle, slug in _HINTS.items():
        if needle in low and slug not in slugs:
            slugs.append(slug)
    return slugs or ["general_assistant"]
