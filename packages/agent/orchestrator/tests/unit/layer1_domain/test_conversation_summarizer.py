from unittest.mock import MagicMock

from app.layer1_domain.entities.planner.conversation_summarizer import (
    ConversationSummarizer,
    ConversationSummaryResponse,
    ConversationTurn,
)


def _turns():
    return [
        ConversationTurn(role="user", content="Create a material master for product X"),
        ConversationTurn(role="assistant", content="I created draft change request CR-42."),
        ConversationTurn(role="user", content=""),  # dropped
    ]


def test_build_transcript_labels_speakers_and_drops_empty():
    transcript = ConversationSummarizer.build_transcript(_turns())
    assert transcript == (
        "User: Create a material master for product X\n"
        "Assistant: I created draft change request CR-42."
    )


def test_summarize_returns_none_when_no_history():
    summarizer = ConversationSummarizer()
    llm = MagicMock()
    result = summarizer.summarize(
        turns=[ConversationTurn(role="user", content="   ")],
        latest_request="summarize",
        llm_provider=llm,
        logger=MagicMock(),
    )
    assert result is None
    llm.complete_with_format.assert_not_called()


def test_summarize_calls_llm_with_transcript_and_summary_model():
    summarizer = ConversationSummarizer()
    expected = ConversationSummaryResponse(
        summary="We worked on a material master.",
        what_the_system_did="Created draft change request CR-42.",
        recommended_next_steps=["Review CR-42", "Submit for approval"],
    )
    llm = MagicMock()
    llm.complete_with_format.return_value = expected

    result = summarizer.summarize(
        turns=_turns(),
        latest_request="summarize the conversation",
        llm_provider=llm,
        logger=MagicMock(),
    )

    assert result is expected
    call = llm.complete_with_format.call_args
    assert call.kwargs["response_model"] is ConversationSummaryResponse
    messages = call.kwargs["messages"]
    user_content = messages[-1]["content"]
    assert "CR-42" in user_content
    assert "Create a material master for product X" in user_content


def test_render_produces_all_sections():
    rendered = ConversationSummarizer.render(
        ConversationSummaryResponse(
            summary="Overview here.",
            what_the_system_did="Ran a workflow.",
            recommended_next_steps=["Do A", "Do B"],
        )
    )
    assert "**Summary**" in rendered
    assert "Overview here." in rendered
    assert "**What the system did**" in rendered
    assert "**Recommended next steps**" in rendered
    assert "- Do A" in rendered
    assert "- Do B" in rendered


def test_render_handles_none_summary():
    rendered = ConversationSummarizer.render(None)
    assert rendered == ConversationSummarizer.EMPTY_MESSAGE
