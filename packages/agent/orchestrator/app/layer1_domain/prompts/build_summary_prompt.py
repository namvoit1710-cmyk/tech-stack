"""Prompt for summarizing a whole conversation (SA-1308).

The summary must cover the three things the ticket asks for:
1. what the conversation was about (content),
2. a brief of what the system actually did, and
3. recommended next steps.
"""

CONVERSATION_SUMMARY_SYSTEM_PROMPT = """
You are the conversation summarizer for the SimpleMDG master-data-governance assistant.
You are given the full transcript of a conversation between a business user and the system
(the user, the orchestrator, and business agents). Produce a faithful, concise summary.

Rules:
1. Summarize ONLY what is present in the transcript. Never invent tasks, results, or data.
2. `summary`: 2-5 sentences describing what the conversation was about, in plain business
   language a non-technical master-data user understands.
3. `what_the_system_did`: a brief of the concrete actions the system/agents took (workflows
   run, records or templates touched, questions asked). If the system did not do anything
   concrete yet, say so plainly.
4. `recommended_next_steps`: 1-4 short, actionable next steps that follow from where the
   conversation left off. If nothing sensible follows, return an empty list.
5. Be honest about gaps. Do not pad. No markdown fences.
""".strip()


def build_summary_prompt(transcript: str, latest_request: str = "") -> list[dict[str, str]]:
    """Build chat messages for conversation summarization."""
    user_sections = [f"CONVERSATION TRANSCRIPT (oldest first):\n{transcript.strip()}"]
    normalized_request = (latest_request or "").strip()
    if normalized_request:
        user_sections.append(
            "The user's latest message (the summary request itself) was:\n"
            f"{normalized_request}"
        )
    user_sections.append(
        "Summarize the conversation above into `summary`, `what_the_system_did`, and "
        "`recommended_next_steps`."
    )
    return [
        {"role": "system", "content": CONVERSATION_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_sections)},
    ]
