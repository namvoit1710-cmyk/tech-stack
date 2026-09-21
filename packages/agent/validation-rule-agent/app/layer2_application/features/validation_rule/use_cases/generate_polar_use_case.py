"""Dedicated use-case: generate a polar transform expression.

Framework-independent. Any governance/LLM failure returns a 200 AgentResponse
error envelope (consistent with the other use-cases). No `Any`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.layer1_domain.entities.agent_response import AgentResponse
from app.layer1_domain.entities.template_field import TemplateField
from app.layer2_application.interfaces.ports import GovernancePort, PolarGeneratorPort


@dataclass
class GeneratePolarInput:
    message: str = ""
    template_id: str = ""

    @classmethod
    def from_params(cls, message: str, params: dict[str, object]) -> "GeneratePolarInput":
        template_id = params.get("template_id")
        return cls(message=message, template_id=template_id if isinstance(template_id, str) else "")


class GeneratePolarUseCase:
    def __init__(
        self,
        polar_generator: PolarGeneratorPort,
        governance: GovernancePort,
        agent_type: str,
    ) -> None:
        self._polar_generator = polar_generator
        self._governance = governance
        self._agent_type = agent_type

    async def execute(self, request: GeneratePolarInput) -> AgentResponse:
        try:
            header_from_template: dict[str, object] | None = None
            if request.template_id:
                fields = self._governance.get_template_fields(request.template_id)
                _, header_from_template = TemplateField.build_header(fields)

            polar = await self._polar_generator.generate(request.message, header=header_from_template)
            return AgentResponse.polar(polar, self._agent_type)
        except Exception as e:
            logging.exception("generate_polar_failed")
            return AgentResponse.error(e, self._agent_type)
