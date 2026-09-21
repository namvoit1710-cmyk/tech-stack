"""REST controllers exposing the Validation Rule Agent's dedicated flows.

Layer 3 adapter. Each endpoint maps 1:1 to a dedicated Layer-2 use-case resolved
(typed via cast) from the app container, and returns its AgentResponse as a dict.
Request bodies are plain dataclasses (rendered in /docs); no business logic here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.validation_rule.use_cases.generate_polar_use_case import (
    GeneratePolarInput,
    GeneratePolarUseCase,
)
from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import (
    ExecuteValidationInput,
    ExecuteValidationUseCase,
)
from app.layer2_application.features.validation_rule.use_cases.validate_by_rules_use_case import (
    ValidateByRulesInput,
    ValidateByRulesUseCase,
)

logger = logging.getLogger(__name__)


# --- DTOs (data transfer only; the HTTP request boundary) ---
@dataclass
class GeneratePolarRequestBody:
    message: str
    template_id: str = ""


@dataclass
class ValidateRequestBody:
    message: str = ""
    file_id: str | None = None
    header: dict[str, object] | None = None
    template_id: str = ""


@dataclass
class ValidateByRulesRequestBody:
    rules: list[dict[str, object]] | dict[str, object]
    file_id: str | None = None
    header: dict[str, object] | None = None
    template_id: str = ""


# --- Dependency getters (typed via cast; app.state is dynamic) ---
def get_generate_polar_use_case(request: Request) -> GeneratePolarUseCase:
    return cast(GeneratePolarUseCase, request.app.state.container["generate_polar_use_case"])


def get_execute_validation_use_case(request: Request) -> ExecuteValidationUseCase:
    return cast(ExecuteValidationUseCase, request.app.state.container["execute_validation_use_case"])


def get_validate_by_rules_use_case(request: Request) -> ValidateByRulesUseCase:
    return cast(ValidateByRulesUseCase, request.app.state.container["validate_by_rules_use_case"])


# --- Router ---
validation_rule_router = APIRouter(tags=["validation-rule"])


@validation_rule_router.post("/generate-polar")
async def generate_polar(
    body: GeneratePolarRequestBody,
    use_case: GeneratePolarUseCase = Depends(get_generate_polar_use_case),
) -> dict[str, object]:
    """Generate a polar transform expression from a natural-language request."""
    response = await use_case.execute(
        GeneratePolarInput(message=body.message, template_id=body.template_id)
    )
    return response.to_dict()


@validation_rule_router.post("/validate")
async def validate(
    body: ValidateRequestBody,
    use_case: ExecuteValidationUseCase = Depends(get_execute_validation_use_case),
) -> dict[str, object]:
    """Run the message->planner validation flow (optionally against a file). For a
    caller-supplied rule plan use POST /validate-by-rules instead."""
    response = await use_case.execute(
        ExecuteValidationInput(
            message=body.message,
            file_id=body.file_id,
            header=body.header,
            template_id=body.template_id,
        )
    )
    return response.to_dict()


@validation_rule_router.post("/validate-by-rules")
async def validate_by_rules(
    body: ValidateByRulesRequestBody,
    use_case: ValidateByRulesUseCase = Depends(get_validate_by_rules_use_case),
) -> dict[str, object]:
    """Execute a caller-supplied rule plan (deterministic; no LLM planner)."""
    response = await use_case.execute(
        ValidateByRulesInput(
            rules=body.rules,
            file_id=body.file_id,
            header=body.header,
            template_id=body.template_id,
        )
    )
    return response.to_dict()
