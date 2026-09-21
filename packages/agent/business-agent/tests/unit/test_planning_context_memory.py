"""SA-1436 — business-agent side: recalled memory reaches the planner as bounded context."""
from agent_sdk.layer1_domain.entities.agent_shared_state import GlobalContext, Metadata, Task

from app.layer1_domain.entities.planning_context import AgentMemoryDTO, PlanningContext


def _planning_context(long_term_memories=None) -> PlanningContext:
    """Build a PlanningContext directly (the orchestrator constructs it field-by-field;
    there is no from_dict on this context)."""
    return PlanningContext(
        business_intent="bi",
        global_context=GlobalContext(),
        current_task=Task(id="task-1", execute_by=None, goal="Need help"),
        metadata=Metadata(conv_id="c1", checkpoint_id="CP-01"),
        long_term_memories=long_term_memories or [],
    )


def test_agent_memory_dto_from_dict():
    dto = AgentMemoryDTO.from_dict(
        {"memory_id": "m1", "memory_type": "preference", "scope": "user", "content": "x", "score": 0.9}
    )
    assert dto.memory_id == "m1"
    assert dto.memory_type == "preference"
    assert dto.score == 0.9


def test_planning_context_carries_long_term_memories():
    ctx = _planning_context(
        long_term_memories=[AgentMemoryDTO("m1", "preference", "user", "minimal fixes")]
    )
    assert len(ctx.long_term_memories) == 1
    assert ctx.long_term_memories[0].content == "minimal fixes"


def test_planning_context_defaults_empty_memories():
    ctx = _planning_context()
    assert ctx.long_term_memories == []


def test_plan_prompt_renders_bounded_memory_block():
    from app.layer4_frameworks.planners.llm_plan_generator import LLMPlanGenerator

    memories = [AgentMemoryDTO("m1", "preference", "user", "User prefers minimal YAGNI patch files.")]
    block = LLMPlanGenerator._build_long_term_memory_block(memories)
    assert "LONG_TERM_MEMORY" in block
    assert "The current user request wins over memory." in block
    assert "User prefers minimal YAGNI patch files." in block
    # empty memory renders nothing, leaving the prompt unchanged
    assert LLMPlanGenerator._build_long_term_memory_block([]) == ""
