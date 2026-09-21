"""Regression tests for HITL public API surface and example coverage.

Covers:
- HITL-related exports are present in agent_sdk.__all__
- HITL example file exists and does not import langgraph or langgraph.* directly
- End-to-end HITL flow: execute → interrupted, resume → completed (using in-memory graph)
- Resume with an explicit interrupt_id is accepted without error
- ResumeAgentOutput mirrors ExecuteAgentOutput shape (both have interrupted + interrupt_payload)
- HitlInterruptPayload metadata dict is mutable and defaults to empty dict
"""

import ast
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, Interrupt

from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime
from tests.helpers.testing import StubLogger as _StubLogger
from tests.helpers.testing import StubMonitor as _StubMonitor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = Path(__file__).parents[2] / "examples"


def _top_level_import_roots(path: Path) -> list[str]:
    """Return the top-level module names (e.g. 'langgraph') imported in *path*."""
    tree = ast.parse(path.read_text())
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.append(node.module.split(".")[0])
    return roots


# ---------------------------------------------------------------------------
# 1. Public API: HITL exports in agent_sdk.__all__
# ---------------------------------------------------------------------------


def test_hitl_interrupt_payload_in_agent_sdk_all():
    import agent_sdk

    assert (
        "HitlInterruptPayload" in agent_sdk.__all__
    ), "HitlInterruptPayload must be in agent_sdk.__all__"


def test_hitl_resume_command_in_agent_sdk_all():
    import agent_sdk

    assert (
        "HitlResumeCommand" in agent_sdk.__all__
    ), "HitlResumeCommand must be in agent_sdk.__all__"


def test_resume_agent_input_in_agent_sdk_all():
    import agent_sdk

    assert (
        "ResumeAgentInput" in agent_sdk.__all__
    ), "ResumeAgentInput must be in agent_sdk.__all__"


def test_resume_agent_output_in_agent_sdk_all():
    import agent_sdk

    assert (
        "ResumeAgentOutput" in agent_sdk.__all__
    ), "ResumeAgentOutput must be in agent_sdk.__all__"


def test_resume_agent_use_case_in_agent_sdk_all():
    import agent_sdk

    assert (
        "ResumeAgentUseCase" in agent_sdk.__all__
    ), "ResumeAgentUseCase must be in agent_sdk.__all__"


def test_interrupt_in_agent_sdk_all():
    import agent_sdk

    assert (
        "interrupt" in agent_sdk.__all__
    ), "interrupt (langgraph.types.interrupt) must be in agent_sdk.__all__"


def test_interrupt_is_callable():
    """The re-exported interrupt helper should be callable."""
    from agent_sdk import interrupt

    assert callable(interrupt), "agent_sdk.interrupt must be callable"


# ---------------------------------------------------------------------------
# 2. HITL example: file exists and avoids raw langgraph imports
# ---------------------------------------------------------------------------


def test_hitl_agent_example_exists():
    """examples/hitl_agent_example.py must exist."""
    example_path = _EXAMPLES_DIR / "hitl_agent_example.py"
    assert example_path.exists(), (
        f"hitl_agent_example.py not found at {example_path}. "
        "Create it as the canonical HITL example."
    )


def test_hitl_agent_example_does_not_import_langgraph_directly():
    """The HITL example must not import langgraph directly; use agent_sdk.interrupt."""
    example_path = _EXAMPLES_DIR / "hitl_agent_example.py"
    if not example_path.exists():
        pytest.skip("hitl_agent_example.py does not exist yet")

    roots = _top_level_import_roots(example_path)
    assert "langgraph" not in roots, (
        "hitl_agent_example.py must not import langgraph directly. "
        "Use `from agent_sdk import interrupt` instead."
    )


def test_hitl_agent_example_imports_from_agent_sdk():
    """The HITL example should import its key symbols from agent_sdk."""
    example_path = _EXAMPLES_DIR / "hitl_agent_example.py"
    if not example_path.exists():
        pytest.skip("hitl_agent_example.py does not exist yet")

    tree = ast.parse(example_path.read_text())
    agent_sdk_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agent_sdk":
            for alias in node.names:
                agent_sdk_imports.append(alias.name)

    assert (
        "interrupt" in agent_sdk_imports
    ), "hitl_agent_example.py must import 'interrupt' from agent_sdk"
    assert (
        "AgentGraphBuilder" in agent_sdk_imports
    ), "hitl_agent_example.py must import 'AgentGraphBuilder' from agent_sdk"


# ---------------------------------------------------------------------------
# 3. End-to-end HITL flow (in-memory, no real LLM)
#    Execute → interrupted, then Resume → completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_then_resume_end_to_end():
    """Full HITL cycle: execute interrupted → resume to completion.

    Uses a real in-memory LangGraph with MemorySaver so both the interrupt
    surfacing and the checkpoint-based resume are exercised.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph
    from langgraph.types import interrupt as lg_interrupt

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    # Build a simple 2-node graph:
    #   ask_human: calls interrupt() to pause
    #   respond:   runs after the human resumes
    builder = StateGraph(dict)

    def ask_human(state: dict) -> dict:
        answer = lg_interrupt("Do you approve?")
        return {"human_answer": answer}

    def respond(state: dict) -> dict:
        return {"message": f"Completed with: {state.get('human_answer', 'no answer')}"}

    builder.add_node("ask_human", ask_human)
    builder.add_node("respond", respond)
    builder.set_entry_point("ask_human")
    builder.add_edge("ask_human", "respond")

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    logger = _StubLogger()
    monitor = _StubMonitor()
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    runtime = LangGraphRuntime(graph)
    execute_uc = ExecuteAgentUseCase(logger=logger, monitor=monitor, agent_graph=graph)
    resume_uc = ResumeAgentUseCase(
        logger=logger, monitor=monitor, agent_runtime=runtime
    )
    exec_input = ExecuteAgentInput(
        message="start",
        conv_id="e2e-conv-1",
        user_id="user-e2e",
        tenant_id="tenant-e2e",
    )
    exec_result = await execute_uc.execute(exec_input)

    assert exec_result.interrupted is True, "First execution should be interrupted"
    assert exec_result.status == "interrupted"
    assert exec_result.interrupt_payload is not None
    assert exec_result.interrupt_payload.thread_id == "e2e-conv-1"

    # --- Resume: provide human answer ---
    resume_input = ResumeAgentInput(
        thread_id="e2e-conv-1",
        resume_value="yes, approved",
    )
    resume_result = await resume_uc.execute(resume_input)

    assert resume_result.interrupted is False, "Resumed graph should complete"
    assert resume_result.status == "success"


@pytest.mark.asyncio
async def test_execute_interrupt_payload_value_is_the_question():
    """interrupt_payload.value should match the argument passed to interrupt()."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph
    from langgraph.types import interrupt as lg_interrupt

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    builder = StateGraph(dict)

    def ask_human(state: dict) -> dict:
        lg_interrupt({"question": "Confirm step?", "step": 1})
        return {}

    builder.add_node("ask_human", ask_human)
    builder.set_entry_point("ask_human")

    graph = builder.compile(checkpointer=MemorySaver())
    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=graph
    )
    result = await use_case.execute(
        ExecuteAgentInput(
            message="go",
            conv_id="e2e-conv-2",
            user_id="u1",
            tenant_id="t1",
        )
    )

    assert result.interrupted is True
    assert result.interrupt_payload.value == {"question": "Confirm step?", "step": 1}


@pytest.mark.asyncio
async def test_resume_with_explicit_interrupt_id_accepted():
    """Resuming with an explicit interrupt_id should not raise an error."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    formatted_state = {"formatted_response": {"content": "done", "status": "success"}}
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    # Providing interrupt_id should not cause any error
    result = await use_case.execute(
        ResumeAgentInput(
            thread_id="conv-id-check",
            resume_value="approved",
            interrupt_id="int-xyz",
        )
    )
    assert result.status == "success"
    assert result.error is None
    mock_graph.ainvoke.assert_called_once_with(
        Command(resume={"int-xyz": "approved"}), mock.ANY
    )


# ---------------------------------------------------------------------------
# 4. Resume output mirrors execute output shape
# ---------------------------------------------------------------------------


def test_resume_agent_output_has_interrupted_field():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentOutput,
    )

    out = ResumeAgentOutput()
    assert hasattr(
        out, "interrupted"
    ), "ResumeAgentOutput must have 'interrupted' field"
    assert out.interrupted is False


def test_resume_agent_output_has_interrupt_payload_field():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentOutput,
    )

    out = ResumeAgentOutput()
    assert hasattr(
        out, "interrupt_payload"
    ), "ResumeAgentOutput must have 'interrupt_payload' field"
    assert out.interrupt_payload is None


def test_resume_agent_output_has_duration_ms():
    """duration_ms should be set after execute() completes."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentOutput,
    )

    out = ResumeAgentOutput()
    assert hasattr(
        out, "duration_ms"
    ), "ResumeAgentOutput must have 'duration_ms' field"


@pytest.mark.asyncio
async def test_resume_duration_ms_is_set():
    """duration_ms should be non-negative after a real execute() call."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    formatted_state = {"formatted_response": {"content": "ok", "status": "success"}}
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=_StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t1", resume_value="yes")
    )
    assert result.duration_ms >= 0.0


# ---------------------------------------------------------------------------
# 5. HitlInterruptPayload metadata defaults
# ---------------------------------------------------------------------------


def test_hitl_interrupt_payload_metadata_defaults_to_empty_dict():
    """metadata should default to {} and be mutable."""
    from agent_sdk import HitlInterruptPayload

    payload = HitlInterruptPayload(thread_id="t1", interrupt_id="i1", value="question")
    assert payload.metadata == {}
    # Must be a fresh mutable dict per instance (not shared)
    payload.metadata["node"] = "review_step"
    assert payload.metadata["node"] == "review_step"


def test_hitl_interrupt_payload_metadata_not_shared_between_instances():
    """Each HitlInterruptPayload instance must have its own metadata dict."""
    from agent_sdk import HitlInterruptPayload

    p1 = HitlInterruptPayload(thread_id="t1", interrupt_id="i1", value="q1")
    p2 = HitlInterruptPayload(thread_id="t2", interrupt_id="i2", value="q2")
    p1.metadata["key"] = "val"
    assert (
        "key" not in p2.metadata
    ), "metadata dicts must not be shared between HitlInterruptPayload instances"


# ---------------------------------------------------------------------------
# 6. Monitor tracks correct metrics during HITL flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_success_metric_tracked():
    """agent_resume_success_with_format metric should be tracked on successful resume."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    formatted_state = {"formatted_response": {"content": "result", "status": "success"}}
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    monitor = _StubMonitor()
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=monitor,
        agent_runtime=LangGraphRuntime(mock_graph),
    )
    await use_case.execute(ResumeAgentInput(thread_id="t1", resume_value="yes"))

    assert monitor.tracked.get("agent_resume_success_with_format", 0) >= 1


@pytest.mark.asyncio
async def test_resume_interrupted_metric_tracked():
    """agent_resume_interrupted metric should be tracked when resumed graph re-interrupts."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    interrupt_obj = Interrupt(value="step 2?", id="int-s2")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    monitor = _StubMonitor()
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=monitor,
        agent_runtime=LangGraphRuntime(mock_graph),
    )
    await use_case.execute(ResumeAgentInput(thread_id="t1", resume_value="go"))

    assert monitor.tracked.get("agent_resume_interrupted", 0) >= 1


@pytest.mark.asyncio
async def test_resume_error_metric_tracked():
    """agent_resume_error metric should be tracked on unexpected exception."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("crash"))

    monitor = _StubMonitor()
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=monitor,
        agent_runtime=LangGraphRuntime(mock_graph),
    )
    await use_case.execute(ResumeAgentInput(thread_id="t1", resume_value="go"))

    assert monitor.tracked.get("agent_resume_error", 0) >= 1


# ---------------------------------------------------------------------------
# 7. LangGraph v1.x state __interrupt__ handling (no mocks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_state_interrupt_tracked_in_monitor():
    """agent_execute_interrupted metric should be tracked when __interrupt__ is in state."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph
    from langgraph.types import interrupt as lg_interrupt

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    builder = StateGraph(dict)

    def ask_human(state: dict) -> dict:
        lg_interrupt("approve?")
        return {}

    builder.add_node("ask_human", ask_human)
    builder.set_entry_point("ask_human")

    graph = builder.compile(checkpointer=MemorySaver())
    monitor = _StubMonitor()
    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=monitor, agent_graph=graph
    )
    await use_case.execute(
        ExecuteAgentInput(
            message="go", conv_id="c-monitor-1", user_id="u1", tenant_id="t1"
        )
    )

    assert monitor.tracked.get("agent_execute_interrupted", 0) >= 1


@pytest.mark.asyncio
async def test_resume_state_interrupt_multi_step():
    """When a resumed graph pauses at a second interrupt(), it returns interrupted output.

    This tests the state __interrupt__ handling in ResumeAgentUseCase.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph
    from langgraph.types import interrupt as lg_interrupt

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    builder = StateGraph(dict)

    def step_1(state: dict) -> dict:
        answer = lg_interrupt("Step 1: approve?")
        return {"a1": answer}

    def step_2(state: dict) -> dict:
        answer = lg_interrupt("Step 2: confirm?")
        return {"a2": answer}

    def finish(state: dict) -> dict:
        return {"message": "all done"}

    builder.add_node("step_1", step_1)
    builder.add_node("step_2", step_2)
    builder.add_node("finish", finish)
    builder.set_entry_point("step_1")
    builder.add_edge("step_1", "step_2")
    builder.add_edge("step_2", "finish")

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    logger = _StubLogger()
    monitor = _StubMonitor()
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    runtime = LangGraphRuntime(graph)
    execute_uc = ExecuteAgentUseCase(logger=logger, monitor=monitor, agent_graph=graph)
    resume_uc = ResumeAgentUseCase(
        logger=logger, monitor=monitor, agent_runtime=runtime
    )
    exec_result = await execute_uc.execute(
        ExecuteAgentInput(
            message="start",
            conv_id="multi-conv-1",
            user_id="u1",
            tenant_id="t1",
        )
    )
    assert exec_result.interrupted is True
    assert exec_result.interrupt_payload.value == "Step 1: approve?"

    # Resume step 1 → interrupted at step 2
    resume1_result = await resume_uc.execute(
        ResumeAgentInput(thread_id="multi-conv-1", resume_value="yes step 1")
    )
    assert resume1_result.interrupted is True, "Should pause at step 2"
    assert resume1_result.interrupt_payload is not None
    assert resume1_result.interrupt_payload.value == "Step 2: confirm?"

    # Resume step 2 → completion
    resume2_result = await resume_uc.execute(
        ResumeAgentInput(thread_id="multi-conv-1", resume_value="yes step 2")
    )
    assert resume2_result.interrupted is False
    assert resume2_result.status == "success"


# ---------------------------------------------------------------------------
# 8. Flexible terminal graph output: agent_result without formatted_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_with_agent_result_only_exposes_full_agent_data():
    """ResumeAgentUseCase: final state with only agent_result (no formatted_response)
    must NOT return generic success with empty data; it must expose the full
    agent_result via ResumeAgentOutput.agent_data."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    final_state = {
        "agent_result": {
            "content": "Workflow plan status: plan_ready",
            "status": "plan_ready",
            "plan": {"steps": []},
        }
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t-agent-result", resume_value="approved")
    )

    assert result.status != "success" or result.agent_data != {}, (
        "ResumeAgentOutput must not return empty agent_data when final_state contains agent_result. "
        f"Got status={result.status!r}, agent_data={result.agent_data!r}"
    )
    assert result.agent_data.get("content") == "Workflow plan status: plan_ready" or (
        result.agent_data == final_state["agent_result"]
    ), (
        "ResumeAgentOutput.agent_data must contain the full agent_result dict. "
        f"Got agent_data={result.agent_data!r}"
    )


@pytest.mark.asyncio
async def test_resume_with_agent_result_preserves_status():
    """ResumeAgentUseCase: final state with agent_result.status must be reflected
    in ResumeAgentOutput.status (not forced to 'success')."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    final_state = {
        "agent_result": {
            "content": "Workflow plan status: plan_ready",
            "status": "plan_ready",
            "plan": {"steps": []},
        }
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t-agent-result-status", resume_value="approved")
    )

    assert result.status == "plan_ready", (
        "ResumeAgentOutput.status must reflect agent_result.status='plan_ready', "
        f"got {result.status!r}"
    )


@pytest.mark.asyncio
async def test_execute_with_agent_result_only_exposes_full_agent_data():
    """ExecuteAgentUseCase: final state with only agent_result (no formatted_response)
    must expose the full agent_result via ExecuteAgentOutput.agent_data."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    final_state = {
        "agent_result": {
            "content": "Plan generated",
            "status": "plan_ready",
            "plan": {"steps": ["step1", "step2"]},
        }
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final_state)

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    result = await use_case.execute(
        ExecuteAgentInput(
            message="generate plan",
            conv_id="t-exec-agent-result",
            user_id="u1",
            tenant_id="t1",
        )
    )

    assert result.agent_data != {}, (
        "ExecuteAgentOutput must not return empty agent_data when final_state contains agent_result. "
        f"Got agent_data={result.agent_data!r}"
    )
    assert result.agent_data == final_state["agent_result"], (
        "ExecuteAgentOutput.agent_data must equal the full agent_result dict. "
        f"Got agent_data={result.agent_data!r}"
    )
