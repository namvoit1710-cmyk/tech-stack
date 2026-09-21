"""Unit specs for the layer4 reviewer components: ReviewerResolver + ReviewerParser.

White-box, grounded in source:
- app/layer4_frameworks/reviewer/reviewer_resolver.py
- app/layer4_frameworks/reviewer/reviewer_parser.py
- app/layer1_domain/entities/reviewer.py (ReviewResponse.from_dict)
- app/layer1_domain/entities/review_context.py (ReviewContextType, ReviewStatus)

ReviewerResolver is a pure sync factory (one llm collaborator). ReviewerParser
normalizes an LLM response into a ReviewResponse. Cases: happy, edge, invalid
input, boundary, failure path.
"""

import json

import pytest

from app.layer1_domain.entities.review_context import ReviewContextType, ReviewStatus
from app.layer1_domain.entities.reviewer import ReviewResponse
from app.layer4_frameworks.reviewer.llm_review_generator import LLMReviewGenerator
from app.layer4_frameworks.reviewer.reviewer_parser import ReviewerParser
from app.layer4_frameworks.reviewer.reviewer_resolver import ReviewerResolver


_LLM_SENTINEL = object()


# =====================================================================
# ReviewerResolver.resolve
# =====================================================================

# --- happy path: LLM context -> LLMReviewGenerator carrying the llm ---
def test_resolve_returns_llm_review_generator():
    resolver = ReviewerResolver(llm=_LLM_SENTINEL)

    generator = resolver.resolve(ReviewContextType.LLM)

    assert isinstance(generator, LLMReviewGenerator)


# --- edge: a new generator instance per resolve call ---
def test_resolve_returns_new_instance_each_call():
    resolver = ReviewerResolver(llm=_LLM_SENTINEL)

    assert resolver.resolve(ReviewContextType.LLM) is not resolver.resolve(
        ReviewContextType.LLM
    )


# --- failure path: LLM branch with no llm raises (source lines 17-18) ---
def test_resolve_requires_llm_for_llm_branch():
    resolver = ReviewerResolver(llm=None)

    with pytest.raises(
        ValueError, match="No OpenAI service provided for LLM planner"
    ):
        resolver.resolve(ReviewContextType.LLM)


# --- invalid input: unknown context type hits the else branch (source line 22) ---
def test_resolve_rejects_unknown_context_type():
    resolver = ReviewerResolver(llm=_LLM_SENTINEL)

    with pytest.raises(ValueError, match="No planner registered for"):
        resolver.resolve("not-a-review-context")


# --- boundary: empty-string llm is falsy -> ValueError on the LLM branch ---
def test_resolve_treats_empty_llm_as_missing():
    resolver = ReviewerResolver(llm="")

    with pytest.raises(
        ValueError, match="No OpenAI service provided for LLM planner"
    ):
        resolver.resolve(ReviewContextType.LLM)


# =====================================================================
# ReviewerParser
# =====================================================================

_VALID_REVIEW = {
    "evaluation": "success",
    "reason": "looks good",
    "nextstep": "continue",
}


# --- happy path: plain JSON string -> ReviewResponse ---
def test_parse_response_from_json_string():
    parsed = ReviewerParser()._parse_response(json.dumps(_VALID_REVIEW))

    assert isinstance(parsed, ReviewResponse)
    assert parsed.evaluation == ReviewStatus.SUCCESS
    assert parsed.reason == "looks good"
    assert parsed.nextstep == "continue"


# --- happy path: already-a-dict passes straight through normalize (source line 44-45) ---
def test_normalize_returns_dict_unchanged():
    assert ReviewerParser().normalize_reviewer_response(_VALID_REVIEW) == _VALID_REVIEW


# --- happy path: list-of-blocks with a text block is parsed (source lines 24-33) ---
def test_normalize_reads_text_block_from_list():
    blocks = [{"type": "text", "text": json.dumps(_VALID_REVIEW)}]

    assert ReviewerParser().normalize_reviewer_response(blocks) == _VALID_REVIEW


# --- edge: escaped-JSON fallback path via unicode_escape (source lines 36-39) ---
def test_normalize_handles_escaped_inner_json():
    escaped = '{\\"evaluation\\": \\"fail\\", \\"reason\\": \\"nope\\"}'
    blocks = [{"type": "text", "text": escaped}]

    assert ReviewerParser().normalize_reviewer_response(blocks) == {
        "evaluation": "fail",
        "reason": "nope",
    }


# --- boundary: reason defaults to "" when absent (source reviewer.py line 22) ---
def test_parse_response_defaults_missing_reason_to_empty_string():
    parsed = ReviewerParser()._parse_response(json.dumps({"evaluation": "fail"}))

    assert parsed.evaluation == ReviewStatus.FAIL
    assert parsed.reason == ""
    assert parsed.nextstep is None


# --- invalid input: list with no text block -> "No text block found" (source line 42) ---
def test_normalize_rejects_list_without_text_block():
    with pytest.raises(ValueError, match="No text block found"):
        ReviewerParser().normalize_reviewer_response(
            [{"type": "image", "text": json.dumps(_VALID_REVIEW)}]
        )


# --- invalid input: unsupported top-level type (source line 47) ---
def test_normalize_rejects_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported type"):
        ReviewerParser().normalize_reviewer_response(123)


# --- failure path: bad evaluation value is wrapped by _parse_response
#     (reviewer.py raises ValueError, parser wraps as "Failed to parse
#     reviewer response", source parser lines 13-14). ---
def test_parse_response_wraps_invalid_evaluation():
    with pytest.raises(ValueError, match="Failed to parse reviewer response"):
        ReviewerParser()._parse_response(
            json.dumps({"evaluation": "not-a-status", "reason": "x"})
        )


# --- failure path: inner text that is not JSON and not escapable ---
def test_normalize_wraps_unparseable_inner_text():
    with pytest.raises(ValueError, match="Failed to parse inner JSON"):
        ReviewerParser().normalize_reviewer_response(
            [{"type": "text", "text": "definitely not json {"}]
        )
