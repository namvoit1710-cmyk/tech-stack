"""Unit specs for PlannerResolver.resolve (layer4 planner factory).

White-box: PlannerResolver is a pure sync factory taking one collaborator
(`llm`) in __init__. It maps a PlanningContextType to a concrete IPlanGenerator
and raises ValueError for the RULE / unknown branches (source:
app/layer4_frameworks/planners/planner_resolver.py). No async, no mocks below
it beyond a stand-in llm sentinel — the generators only store the llm reference
in their own __init__, so an arbitrary object() is a valid seam here.

Case types covered: happy, edge, invalid input, boundary, failure path.
"""

import pytest

from app.layer1_domain.entities.planning_context import PlanningContextType
from app.layer4_frameworks.planners.llm_plan_generator import LLMPlanGenerator
from app.layer4_frameworks.planners.llm_plan_simple_node_generator import (
    LLMPlanSimpleNodeGenerator,
)
from app.layer4_frameworks.planners.llm_plan_sub_user_chat_generator import (
    LLMPlanSubUserChatGenerator,
)
from app.layer4_frameworks.planners.llm_summary_context import (
    LLMSummaryContextGenerator,
)
from app.layer4_frameworks.planners.planner_resolver import PlannerResolver


_LLM_SENTINEL = object()


# --- happy path: every LLM-backed context type maps to its generator ---
@pytest.mark.parametrize(
    ("context_type", "expected_cls"),
    [
        (PlanningContextType.LLM, LLMPlanGenerator),
        (PlanningContextType.LLM_WITH_SUB_USER_CHAT, LLMPlanSubUserChatGenerator),
        (PlanningContextType.LLM_WITH_SIMPLE_NODE, LLMPlanSimpleNodeGenerator),
        (
            PlanningContextType.LLM_SUMMARY_CONTEXT_GENERATOR,
            LLMSummaryContextGenerator,
        ),
    ],
)
def test_resolve_returns_expected_generator(context_type, expected_cls):
    resolver = PlannerResolver(llm=_LLM_SENTINEL)

    generator = resolver.resolve(context_type)

    assert isinstance(generator, expected_cls)
    # the resolver forwards its llm into the generator (source lines 29-37)
    assert generator.llm is _LLM_SENTINEL


# --- edge: a fresh generator instance is built on each resolve call ---
def test_resolve_returns_new_instance_each_call():
    resolver = PlannerResolver(llm=_LLM_SENTINEL)

    first = resolver.resolve(PlanningContextType.LLM)
    second = resolver.resolve(PlanningContextType.LLM)

    assert first is not second
    assert isinstance(first, LLMPlanGenerator)
    assert isinstance(second, LLMPlanGenerator)


# --- failure path: no llm supplied -> guard raises before any branch ---
@pytest.mark.parametrize(
    "context_type",
    [
        PlanningContextType.LLM,
        PlanningContextType.LLM_WITH_SUB_USER_CHAT,
        PlanningContextType.LLM_WITH_SIMPLE_NODE,
        PlanningContextType.LLM_SUMMARY_CONTEXT_GENERATOR,
    ],
)
def test_resolve_requires_llm(context_type):
    resolver = PlannerResolver(llm=None)

    with pytest.raises(ValueError, match="No LLM provided for LLM planner"):
        resolver.resolve(context_type)


# --- failure path: RULE branch is explicitly unimplemented (source line 34-35) ---
def test_resolve_rejects_rule_planner():
    resolver = PlannerResolver(llm=_LLM_SENTINEL)

    with pytest.raises(ValueError, match="No rule planner provided"):
        resolver.resolve(PlanningContextType.RULE)


# --- invalid input: a declared enum member with no branch falls through
#     to the else (source line 38-39). LLM_WITH_PATCH_INPUT_NODE exists in the
#     enum but PlannerResolver.resolve has no case for it. ---
def test_resolve_rejects_context_type_without_branch():
    resolver = PlannerResolver(llm=_LLM_SENTINEL)

    with pytest.raises(ValueError, match="No planner registered for"):
        resolver.resolve(PlanningContextType.LLM_WITH_PATCH_INPUT_NODE)


# --- invalid input: a non-enum value also hits the else branch ---
def test_resolve_rejects_unknown_value():
    resolver = PlannerResolver(llm=_LLM_SENTINEL)

    with pytest.raises(ValueError, match="No planner registered for"):
        resolver.resolve("not-a-context-type")


# --- boundary: empty-string llm is falsy -> treated as "no llm" by the guard ---
def test_resolve_treats_empty_llm_as_missing():
    resolver = PlannerResolver(llm="")

    with pytest.raises(ValueError, match="No LLM provided for LLM planner"):
        resolver.resolve(PlanningContextType.LLM)
