"""context_budget_example.py — ContextBudgetManager deep dive.

Supervisor agents using small models (gpt-4o-mini, 16 K context) accumulate
long message histories during multi-step tool-calling loops.  Without active
management the context window fills up, causing the LLM to hallucinate,
repeat itself, or silently drop instructions.

``ContextBudgetManager`` solves this by compacting the message history
*before each LLM invocation*.  It offers **five compaction strategies**,
each designed for a different failure mode.  This example demonstrates
all five, explains *when and why* to choose each one, and shows how to
wire the manager into ``ToolAgentBuilder``.

Strategies at a glance
----------------------

+---------------------+------+------------------------------------------------+
| Strategy            | Cost | When to use                                    |
+=====================+======+================================================+
| ``TOOL_RESULT_CLEAR``| Low  | Tool results are large but reasoning history   |
|                     |      | is short.  Clears old tool outputs only.       |
+---------------------+------+------------------------------------------------+
| ``SELECTIVE``       | Low  | (default) Supervisor handles structured        |
|                     |      | business data.  Preserves BUSINESS_DATA        |
|                     |      | messages verbatim; clears old tool results;    |
|                     |      | keeps the last N messages.                     |
+---------------------+------+------------------------------------------------+
| ``HEAD_TAIL``       | Low  | No business payloads; just keep the system     |
|                     |      | prompt + the most recent conversation.         |
|                     |      | Simplest and most aggressive — drops the       |
|                     |      | entire middle of the history.                  |
+---------------------+------+------------------------------------------------+
| ``TIERED``          | Med  | Long-running loops where context pressure      |
|                     |      | varies.  Escalates automatically:              |
|                     |      | light → medium → heavy compaction              |
|                     |      | based on how full the budget is.               |
|                     |      | With an ``ILLMService``, the medium tier uses  |
|                     |      | LLM-based summarization instead of dropping.   |
+---------------------+------+------------------------------------------------+
| ``NONE``            | Free | Disable compaction entirely.  Useful during    |
|                     |      | development or when the model has a very large |
|                     |      | context window (128 K+).                       |
+---------------------+------+------------------------------------------------+

Why mark messages as BUSINESS_DATA?
------------------------------------

Supervisor agents often delegate to sub-agents and pass back structured
payloads — file IDs, content blobs, status objects.  If compaction
*summarises* or *drops* these payloads, the next sub-agent receives
incomplete data and the business workflow breaks silently.

Marking a message ``PayloadType.BUSINESS_DATA`` guarantees it is never
cleared, summarised, or dropped during compaction.  Everything else
(reasoning, status updates, old tool outputs) is ``NARRATIVE`` by
default and is eligible for compaction.

Lost-in-the-middle mitigation
------------------------------

Research shows LLMs pay less attention to information in the middle of
a long context ("lost-in-the-middle" effect).  ``ToolAgentBuilder``
supports a ``system_reminder`` parameter — a short instruction that is
re-injected at the *end* of the message list once the conversation
exceeds ``system_reminder_threshold`` messages.  This keeps critical
instructions salient without manual intervention.

Usage::

    python examples/context_budget_example.py

This example is self-contained and exits 0 without external services.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import (
    CompactionStrategy,
    ContextBudgetConfig,
    ContextBudgetManager,
    PayloadType,
    ToolAgentBuilder,
    make_openai_service,
    run_agent,
    tool,
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def fetch_file_content(file_id: str) -> dict:
    """Retrieve the content of a file by its ID.

    Args:
        file_id: The unique identifier of the file.
    """
    return {
        "file_id": file_id,
        "content": f"[Content of file {file_id}]",
        "size_bytes": 1024,
        "status": "COMPLETED",
    }


# ---------------------------------------------------------------------------
# Helper: build a realistic message history
# ---------------------------------------------------------------------------


def _build_sample_messages(n_tool_rounds: int = 5) -> list[dict]:
    """Build a realistic supervisor message history with tool calls.

    The history has:
      - 1 system prompt (always first)
      - 1 user request
      - N rounds of assistant reasoning + tool results
      - 1 final user follow-up

    This simulates a supervisor that has been running for a while.
    """
    msgs: list[dict] = [
        {"role": "system", "content": "You are a supervisor agent."},
        {"role": "user", "content": "Process file f-001"},
    ]
    for i in range(n_tool_rounds):
        msgs.append({"role": "assistant", "content": f"Reasoning step {i}..."})
        if i == 0:
            # First tool result is business-critical data
            msgs.append(
                {
                    "role": "tool",
                    "content": '{"file_id":"f-001","content":"Important data","status":"ok"}',
                }
            )
        else:
            msgs.append(
                {
                    "role": "tool",
                    "content": f"[tool result {i} — verbose diagnostic output]",
                }
            )
    msgs.append({"role": "user", "content": "Now summarise the result."})
    return msgs


# ---------------------------------------------------------------------------
# Strategy 1: TOOL_RESULT_CLEAR
# ---------------------------------------------------------------------------
# USE WHEN: tool results are large (e.g. full file contents, API responses)
# but the conversational reasoning history is short enough to keep.
#
# HOW IT WORKS: walks backward through the history; any tool message older
# than the last `max_message_history` messages has its content replaced with
# "[cleared]".  Messages marked BUSINESS_DATA are never cleared.
#
# TRADE-OFF: keeps all reasoning intact so the LLM retains full decision
# context, but only saves tokens proportional to tool-result size.
# ---------------------------------------------------------------------------


async def demo_tool_result_clear() -> None:
    """TOOL_RESULT_CLEAR — clear old tool outputs, keep reasoning."""
    config = ContextBudgetConfig(
        max_total_tokens=200,
        max_message_history=4,
        compaction_strategy=CompactionStrategy.TOOL_RESULT_CLEAR,
        compaction_threshold=0.8,
    )
    manager = ContextBudgetManager(config)
    messages = _build_sample_messages(n_tool_rounds=5)

    # Mark the first tool result (index 3) as business data
    payload_types = {3: PayloadType.BUSINESS_DATA}

    before = manager.count_tokens(messages)
    compacted, warnings = await manager.compact(messages, payload_types=payload_types)
    after = manager.count_tokens(compacted)

    print(
        f"TOOL_RESULT_CLEAR: {len(messages)} msgs ({before} tok) → "
        f"{len(compacted)} msgs ({after} tok)"
    )
    # All messages are still present (same count) — only content was cleared
    assert len(compacted) == len(messages)
    # The business-data tool result at index 3 is untouched
    assert "Important data" in compacted[3].get("content", "")
    print("  ✓ Business data preserved, old tool results cleared.\n")


# ---------------------------------------------------------------------------
# Strategy 2: SELECTIVE (default)
# ---------------------------------------------------------------------------
# USE WHEN: the supervisor handles structured business data (file IDs, content
# blobs, status objects) that downstream agents depend on.  This is the
# recommended default for most supervisor agents.
#
# HOW IT WORKS:
#   1. System prompt is always preserved (head).
#   2. The last `max_message_history` messages are always kept (tail).
#   3. Everything in the middle is inspected:
#      - BUSINESS_DATA messages → kept verbatim
#      - tool messages → content replaced with "[cleared — old tool result]"
#      - everything else → dropped
#
# WHY DEFAULT: most supervisor agents need their business payloads intact
# (otherwise sub-agent delegation breaks) but can afford to lose old
# reasoning steps and tool diagnostics.
# ---------------------------------------------------------------------------


async def demo_selective() -> None:
    """SELECTIVE — preserve business payloads, drop old reasoning."""
    config = ContextBudgetConfig(
        max_total_tokens=120,
        max_message_history=6,
        max_tool_result_tokens=2_000,
        compaction_strategy=CompactionStrategy.SELECTIVE,
        compaction_threshold=0.8,
        preserve_business_payloads=True,
    )
    manager = ContextBudgetManager(config)
    messages = _build_sample_messages(n_tool_rounds=5)
    payload_types = {3: PayloadType.BUSINESS_DATA}

    before = manager.count_tokens(messages)
    compacted, warnings = await manager.compact(messages, payload_types=payload_types)
    after = manager.count_tokens(compacted)

    print(
        f"SELECTIVE: {len(messages)} msgs ({before} tok) → "
        f"{len(compacted)} msgs ({after} tok)"
    )
    assert compacted[0]["role"] == "system", "System prompt must be first"
    # Business data is preserved somewhere in the compacted output
    biz = [m for m in compacted if "Important data" in m.get("content", "")]
    assert biz, "Business data must survive selective compaction"
    print("  ✓ System prompt + business data + tail preserved.\n")


# ---------------------------------------------------------------------------
# Strategy 3: HEAD_TAIL
# ---------------------------------------------------------------------------
# USE WHEN: there are no business payloads to protect and you want the
# simplest, most aggressive compaction.
#
# HOW IT WORKS: keeps the system prompt (head) and the last
# `max_message_history` messages (tail).  Everything in between is dropped
# entirely.
#
# TRADE-OFF: extremely effective at reducing tokens, but all mid-conversation
# context is lost.  The LLM essentially "forgets" earlier reasoning.  Only
# use this when earlier context is truly disposable.
# ---------------------------------------------------------------------------


async def demo_head_tail() -> None:
    """HEAD_TAIL — keep system prompt + last N messages."""
    config = ContextBudgetConfig(
        max_total_tokens=200,
        max_message_history=8,
        compaction_strategy=CompactionStrategy.HEAD_TAIL,
        compaction_threshold=0.8,
    )
    manager = ContextBudgetManager(config)

    messages = [{"role": "system", "content": "System prompt."}]
    for i in range(20):
        messages.append({"role": "user", "content": f"Message {i}"})
        messages.append({"role": "assistant", "content": f"Response {i}"})

    before = manager.count_tokens(messages)
    compacted, warnings = await manager.compact(messages)
    after = manager.count_tokens(compacted)

    print(
        f"HEAD_TAIL: {len(messages)} msgs ({before} tok) → "
        f"{len(compacted)} msgs ({after} tok)"
    )
    assert compacted[0]["role"] == "system", "System prompt must be first"
    assert len(compacted) <= config.max_message_history + 1
    print("  ✓ System prompt + last 8 messages kept.\n")


# ---------------------------------------------------------------------------
# Strategy 4: TIERED
# ---------------------------------------------------------------------------
# USE WHEN: the supervisor runs long tool-calling loops where context pressure
# varies over time.  Instead of always applying the same strategy, TIERED
# escalates automatically based on how full the context window is:
#
#   compaction_threshold (80%) → TOOL_RESULT_CLEAR   (light)
#   aggressive_threshold (85%) → SELECTIVE or LLM summarization (medium)
#   danger_threshold     (95%) → HEAD_TAIL           (heavy)
#
# This means:
#   - Early in the conversation: only tool results are cleared (minimal info loss)
#   - As the window fills: reasoning is compacted (moderate info loss)
#   - Near the limit: everything is trimmed to essentials (maximum info loss)
#
# WITH LLM SERVICE: at the medium tier (aggressive_threshold), if an
# ``ILLMService`` is provided, TIERED uses the LLM to *summarise* the
# middle of the conversation instead of dropping it.  This preserves more
# context than SELECTIVE at the cost of one extra LLM call.
#
# WHY USE IT: "set-and-forget" — you don't need to predict how long the
# conversation will be.  The strategy adapts as pressure increases.
# ---------------------------------------------------------------------------


async def demo_tiered() -> None:
    """TIERED — escalating compaction based on context pressure."""
    config = ContextBudgetConfig(
        max_total_tokens=200,
        max_message_history=6,
        compaction_strategy=CompactionStrategy.TIERED,
        compaction_threshold=0.8,
        aggressive_threshold=0.85,
        danger_threshold=0.95,
    )
    manager = ContextBudgetManager(config)
    messages = _build_sample_messages(n_tool_rounds=5)
    payload_types = {3: PayloadType.BUSINESS_DATA}

    before = manager.count_tokens(messages)
    compacted, warnings = await manager.compact(messages, payload_types=payload_types)
    after = manager.count_tokens(compacted)

    pressure = before / config.max_total_tokens
    print(
        f"TIERED: {len(messages)} msgs ({before} tok) → "
        f"{len(compacted)} msgs ({after} tok)"
    )
    print(f"  Context pressure: {pressure:.0%}")
    if pressure >= config.danger_threshold:
        print("  → DANGER tier: HEAD_TAIL applied")
    elif pressure >= config.aggressive_threshold:
        print(
            "  → AGGRESSIVE tier: SELECTIVE applied (LLM summary if service provided)"
        )
    else:
        print("  → LIGHT tier: TOOL_RESULT_CLEAR applied")
    assert compacted[0]["role"] == "system", "System prompt must be first"
    print("  ✓ Tiered compaction applied based on context pressure.\n")


# ---------------------------------------------------------------------------
# Feature: max_tool_result_tokens — pre-processing truncation
# ---------------------------------------------------------------------------
# ContextBudgetManager truncates individual tool results that exceed
# `max_tool_result_tokens` *before* any compaction strategy runs.
# This prevents a single giant tool result (e.g. a full file dump)
# from blowing the budget on its own.
# ---------------------------------------------------------------------------


async def demo_tool_result_truncation() -> None:
    """max_tool_result_tokens — truncate oversized tool results."""
    config = ContextBudgetConfig(
        max_total_tokens=500,
        max_tool_result_tokens=10,  # very small limit for demo
        compaction_strategy=CompactionStrategy.NONE,  # disable compaction
    )
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "tool", "content": "word " * 100},  # ~101 tokens
    ]

    compacted, _ = await manager.compact(messages)
    tool_msg = compacted[1]
    assert "[truncated" in tool_msg["content"], "Tool result should be truncated"
    print("TOOL_RESULT_TRUNCATION:")
    print("  Original tool result: ~101 tokens")
    print(
        f"  After truncation: {manager.count_tokens([tool_msg]) - 4} tokens (limit: 10)"
    )
    print("  ✓ Oversized tool result truncated before compaction.\n")


# ---------------------------------------------------------------------------
# Feature: system_reminder — lost-in-the-middle mitigation
# ---------------------------------------------------------------------------
# When the conversation gets long, the LLM pays less attention to the
# system prompt at the top.  ToolAgentBuilder can re-inject a short
# "reminder" instruction at the END of the message list so critical
# rules stay salient.
#
# This is configured on ToolAgentBuilder, not ContextBudgetManager:
#   system_reminder="Always return file_id in your response."
#   system_reminder_threshold=6   # inject after 6+ messages
# ---------------------------------------------------------------------------


def demo_system_reminder_config() -> None:
    """Show system_reminder configuration on ToolAgentBuilder.

    This demo only shows the configuration — it does not make LLM calls.
    """
    print("SYSTEM_REMINDER (ToolAgentBuilder config):")
    print("  ToolAgentBuilder(")
    print('      system_reminder="Always return file_id in your response.",')
    print("      system_reminder_threshold=6,  # inject after 6+ messages")
    print("  )")
    print("  → After 6+ messages, the reminder is appended as a SystemMessage")
    print("    at the end of the list, keeping critical instructions salient.")
    print("  ✓ Lost-in-the-middle mitigation configured.\n")


# ---------------------------------------------------------------------------
# Wiring: ContextBudgetManager + ToolAgentBuilder
# ---------------------------------------------------------------------------


def build_budget_aware_agent():
    """Build a ToolAgentBuilder with full context budget management.

    This shows the recommended production configuration:
    - TIERED compaction with escalating thresholds
    - system_reminder for lost-in-the-middle mitigation
    - context_budget wired into the builder for automatic compaction
    """
    config = ContextBudgetConfig(
        max_total_tokens=16_000,
        max_message_history=10,
        max_tool_result_tokens=2_000,
        compaction_strategy=CompactionStrategy.TIERED,
        compaction_threshold=0.8,
        aggressive_threshold=0.85,
        danger_threshold=0.95,
        preserve_business_payloads=True,
    )
    manager = ContextBudgetManager(config)

    llm_service = make_openai_service()
    builder = ToolAgentBuilder(
        llm_service=llm_service,
        tools=[fetch_file_content],
        system_prompt=(
            "You are a supervisor agent. "
            "Use fetch_file_content to retrieve file contents when asked."
        ),
        context_budget=manager,
        system_reminder="Always include the file_id in your final response.",
        system_reminder_threshold=6,
    )
    return builder.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_all_demos() -> None:
    """Run all strategy demos in sequence."""
    print("=" * 60)
    print("ContextBudgetManager — Compaction Strategy Deep Dive")
    print("=" * 60)
    print()

    await demo_tool_result_truncation()
    await demo_tool_result_clear()
    await demo_selective()
    await demo_head_tail()
    await demo_tiered()
    demo_system_reminder_config()

    print("=" * 60)
    print("All demos passed.")
    print("=" * 60)


def main() -> None:
    asyncio.run(run_all_demos())
    print("\nStarting agent server...")
    run_agent(agent_graph=build_budget_aware_agent())


if __name__ == "__main__":
    main()
