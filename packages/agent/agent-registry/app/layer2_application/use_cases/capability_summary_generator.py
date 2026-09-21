"""Capability Summary Generator - synthesizes a human-readable capability summary via LLM."""

from agent_sdk.layer2_application.interfaces.chat_completion_service import IChatCompletionService

from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger


class CapabilitySummaryGenerator:
    """Generates an Agent's Capability Summary from its description and attached sources.

    Sources: agent description, tool descriptions, workflow descriptions, child/referenced
    agent descriptions. Duplicate/overlapping raw descriptions are consolidated deterministically
    before being handed to the LLM, which writes the final concise, human-readable summary.
    """

    _SYSTEM_PROMPT = (
        "You are writing the Capability Summary for an AI agent registry used for capability "
        "search. Given an agent's own description plus the descriptions of its attached tools, "
        "workflows, and child agents, produce a concise, keyword-dense capability summary as a "
        "semicolon-separated list of short capability phrases (e.g. 'drafting SAP MDG change "
        "requests; validating change requests for approval; searching SAP master-data "
        "governance rules'). Each phrase should be a short noun or gerund phrase naming ONE "
        "functional responsibility - do not write full sentences, and never start a phrase with "
        "'the agent' or 'this agent'. Consolidate overlapping or duplicate capabilities into a "
        "single phrase instead of repeating them. Do not invent capabilities that are not "
        "present in the input. Respond with plain text only - the phrase list itself, no "
        "headings, no numbering, no markdown."
    )

    def __init__(self, llm_service: IChatCompletionService, logger: ILogger | None = None):
        self._llm_service = llm_service
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def generate(
        self,
        description: str,
        tool_descriptions: list[str] | None = None,
        workflow_descriptions: list[str] | None = None,
        agent_descriptions: list[str] | None = None,
    ) -> str:
        """Synthesize a concise, deduplicated, human-readable capability summary via LLM.

        Args:
            description: The agent's own description.
            tool_descriptions: Descriptions of attached tools.
            workflow_descriptions: Descriptions of attached workflows.
            agent_descriptions: Descriptions of attached child/referenced agents.

        Returns:
            The LLM-written summary, or an empty string if every source is empty
            (no LLM call is made in that case).
        """
        sources = self._collect_unique_sources(
            description, tool_descriptions, workflow_descriptions, agent_descriptions
        )
        if not sources:
            return ""

        user_prompt = (
            f"Agent description:\n{sources[0]}\n\n"
            f"Attached capabilities (from tools, workflows, and child agents):\n"
            + ("\n".join(f"- {source}" for source in sources[1:]) or "(none)")
        )

        # The capability summary is a non-critical, LLM-generated convenience for
        # capability search. If the LLM is unavailable, agent registration must NOT
        # fail — degrade gracefully to an empty summary (consistent with agents that
        # were seeded without one). Otherwise a transient LLM outage 500s every
        # register/update call.
        try:
            summary = await self._llm_service.get_chat_completion(
                system_prompt=self._SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_mode=False,
            )
            return summary.strip()
        except Exception as e:  # noqa: BLE001 - degrade on any LLM/transport failure
            self._logger.warning(
                "capability_summary_generation_failed: %s - registering agent with empty summary",
                str(e),
            )
            return ""

    @staticmethod
    def _collect_unique_sources(
        description: str,
        tool_descriptions: list[str] | None,
        workflow_descriptions: list[str] | None,
        agent_descriptions: list[str] | None,
    ) -> list[str]:
        """Deduplicate (case/whitespace-insensitive) and order sources, description first."""
        ordered_fragments = [
            description,
            *(tool_descriptions or []),
            *(workflow_descriptions or []),
            *(agent_descriptions or []),
        ]

        seen_normalized: set[str] = set()
        unique: list[str] = []
        for fragment in ordered_fragments:
            if not fragment:
                continue
            cleaned = fragment.strip()
            if not cleaned:
                continue
            normalized = cleaned.lower()
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            unique.append(cleaned)

        return unique
