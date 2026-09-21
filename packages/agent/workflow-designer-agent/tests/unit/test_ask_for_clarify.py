from __future__ import annotations

from app.layer4_frameworks.tools.ask_for_clarify import ask_for_clarify


async def test_ask_for_clarify_returns_question() -> None:
    result = await ask_for_clarify.ainvoke(
        {"question": "What should trigger this workflow?", "missing": ["trigger"]}
    )
    assert result["status"] == "needs_clarification"
    assert result["question"] == "What should trigger this workflow?"
    assert result["missing_fields"] == ["trigger"]


async def test_ask_for_clarify_defaults_missing_to_empty_list() -> None:
    result = await ask_for_clarify.ainvoke({"question": "Which report format?"})
    assert result["missing_fields"] == []
