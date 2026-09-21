import pytest

from app.layer1_domain.entities.next_step_suggestion import (
    NextStepKind,
    NextStepSuggestion,
)
from app.layer1_domain.entities.planner import PlannerResponse, PlannerResponseType


def test_from_dict_builds_suggestion_and_strips_whitespace() -> None:
    suggestion = NextStepSuggestion.from_dict(
        {"title": "  Review report  ", "prompt": " Open it ", "kind": "workflow"}
    )

    assert suggestion.title == "Review report"
    assert suggestion.prompt == "Open it"
    assert suggestion.kind == NextStepKind.WORKFLOW


def test_from_dict_defaults_unknown_kind_to_follow_up() -> None:
    suggestion = NextStepSuggestion.from_dict(
        {"title": "Do X", "prompt": "Please do X", "kind": "nonsense"}
    )

    assert suggestion.kind == NextStepKind.FOLLOW_UP


@pytest.mark.parametrize(
    "data",
    [
        {"title": "", "prompt": "p"},
        {"title": "t", "prompt": "   "},
        {"title": "t"},
        {"prompt": "p"},
        {"title": 1, "prompt": "p"},
    ],
)
def test_from_dict_rejects_incomplete_suggestions(data: dict) -> None:
    with pytest.raises(ValueError):
        NextStepSuggestion.from_dict(data)


def test_planner_response_parses_next_steps_and_skips_malformed() -> None:
    response = PlannerResponse.from_dict(
        {
            "type": "summary",
            "answer": "done",
            "steps": [],
            "next_steps": [
                {"title": "Follow up", "prompt": "Do the follow up", "kind": "follow_up"},
                {"title": "", "prompt": "missing title"},
                "not-a-dict",
                {"title": "Ask agent", "prompt": "Hand to agent", "kind": "agent"},
            ],
        }
    )

    assert response.type == PlannerResponseType.SUMMARY
    assert [s.title for s in response.suggestions] == ["Follow up", "Ask agent"]
    assert response.suggestions[1].kind == NextStepKind.AGENT


def test_planner_response_defaults_to_empty_suggestions() -> None:
    response = PlannerResponse.from_dict({"type": "summary", "answer": "done", "steps": []})

    assert response.suggestions == []


def test_from_dict_generates_unique_suggestion_ids() -> None:
    s1 = NextStepSuggestion.from_dict({"title": "A", "prompt": "Do A"})
    s2 = NextStepSuggestion.from_dict({"title": "B", "prompt": "Do B"})

    assert s1.suggestion_id != ""
    assert s2.suggestion_id != ""
    assert s1.suggestion_id != s2.suggestion_id


@pytest.mark.parametrize(
    "data,expected_error",
    [
        ({"title": "x" * 201, "prompt": "p"}, "title exceeds"),
        ({"title": "t", "prompt": "x" * 2001}, "prompt exceeds"),
    ],
)
def test_from_dict_rejects_oversized_fields(data: dict, expected_error: str) -> None:
    with pytest.raises(ValueError, match=expected_error):
        NextStepSuggestion.from_dict(data)


def test_parse_suggestions_caps_at_four() -> None:
    items = [{"title": f"Step {i}", "prompt": f"Do step {i}"} for i in range(6)]
    response = PlannerResponse.from_dict(
        {"type": "summary", "answer": "done", "steps": [], "next_steps": items}
    )

    assert len(response.suggestions) == 4
    assert response.suggestions[0].title == "Step 0"
    assert response.suggestions[3].title == "Step 3"
