"""Unit tests for CapabilitySummaryGenerator."""

from unittest.mock import AsyncMock

import pytest

from app.layer2_application.use_cases.capability_summary_generator import CapabilitySummaryGenerator


class TestCapabilitySummaryGenerator:
    """Test CapabilitySummaryGenerator.generate."""

    @pytest.fixture
    def mock_llm_service(self):
        """Mock IChatCompletionService."""
        llm_service = AsyncMock()
        llm_service.get_chat_completion.return_value = "Handles support tickets and escalates them."
        return llm_service

    @pytest.fixture
    def generator(self, mock_llm_service):
        """Create generator with the mocked LLM service."""
        return CapabilitySummaryGenerator(llm_service=mock_llm_service)

    async def test_calls_llm_with_description_only(self, generator, mock_llm_service):
        """Test the LLM is invoked with the agent description when no other sources exist."""
        summary = await generator.generate(description="Answers customer questions")

        mock_llm_service.get_chat_completion.assert_awaited_once()
        call_kwargs = mock_llm_service.get_chat_completion.call_args.kwargs
        assert "Answers customer questions" in call_kwargs["user_prompt"]
        assert call_kwargs["json_mode"] is False
        assert summary == "Handles support tickets and escalates them."

    async def test_no_sources_returns_empty_string_without_calling_llm(self, generator, mock_llm_service):
        """Test all-empty inputs skip the LLM call entirely."""
        summary = await generator.generate(description="")

        assert summary == ""
        mock_llm_service.get_chat_completion.assert_not_awaited()

    async def test_combines_all_sources_into_the_prompt(self, generator, mock_llm_service):
        """Test description, tools, workflows, and agents are all included in the prompt sent to the LLM."""
        await generator.generate(
            description="Handles support tickets",
            tool_descriptions=["Searches the knowledge base"],
            workflow_descriptions=["Escalates to a human"],
            agent_descriptions=["Drafts email replies"],
        )

        user_prompt = mock_llm_service.get_chat_completion.call_args.kwargs["user_prompt"]
        assert "Handles support tickets" in user_prompt
        assert "Searches the knowledge base" in user_prompt
        assert "Escalates to a human" in user_prompt
        assert "Drafts email replies" in user_prompt

    async def test_deduplicates_case_and_whitespace_insensitively_before_prompting(
        self, generator, mock_llm_service
    ):
        """Test duplicate/overlapping descriptions are consolidated before being sent to the LLM."""
        await generator.generate(
            description="Searches the knowledge base",
            tool_descriptions=["  searches the knowledge base  ", "Sends an email"],
        )

        user_prompt = mock_llm_service.get_chat_completion.call_args.kwargs["user_prompt"]
        assert user_prompt.lower().count("searches the knowledge base") == 1
        assert "Sends an email" in user_prompt

    async def test_ignores_empty_and_whitespace_only_fragments(self, generator, mock_llm_service):
        """Test blank/whitespace-only descriptions from any source are skipped."""
        await generator.generate(
            description="Handles orders",
            tool_descriptions=["", "   ", None],
            workflow_descriptions=[],
            agent_descriptions=["Tracks shipments"],
        )

        user_prompt = mock_llm_service.get_chat_completion.call_args.kwargs["user_prompt"]
        assert "Handles orders" in user_prompt
        assert "Tracks shipments" in user_prompt

    async def test_strips_llm_response_whitespace(self, generator, mock_llm_service):
        """Test the raw LLM response is stripped before being returned."""
        mock_llm_service.get_chat_completion.return_value = "  A tidy summary.  \n"

        summary = await generator.generate(description="Something")

        assert summary == "A tidy summary."

    async def test_system_prompt_instructs_conciseness_and_no_invention(self, generator, mock_llm_service):
        """Test the system prompt asks for a concise, consolidated, non-fabricated summary."""
        await generator.generate(description="Something")

        system_prompt = mock_llm_service.get_chat_completion.call_args.kwargs["system_prompt"]
        assert "concise" in system_prompt.lower()
        assert "do not invent" in system_prompt.lower()

    async def test_system_prompt_requires_searchable_keyword_phrases(self, generator, mock_llm_service):
        """Test the system prompt asks for a semicolon-separated, keyword-dense phrase list rather than narrative prose."""
        await generator.generate(description="Something")

        system_prompt = mock_llm_service.get_chat_completion.call_args.kwargs["system_prompt"]
        assert "semicolon-separated" in system_prompt.lower()
        assert "keyword-dense" in system_prompt.lower()
        assert "the agent" in system_prompt.lower()  # instructing NOT to start phrases this way
        assert "do not write full sentences" in system_prompt.lower()
