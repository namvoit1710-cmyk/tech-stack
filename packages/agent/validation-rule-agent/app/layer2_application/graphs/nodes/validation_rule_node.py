"""Graph node adapter: validate the request and dispatch to the dedicated use-case.

Thin adapter — the flag-based selection lives here (so there are exactly three
use-cases). Domain rules are owned by the ValidationRequest entity. The use-cases
are resolved from the deps container (heterogeneous) via typed casts.
"""
from __future__ import annotations

from typing import cast

from app.layer1_domain.entities.validation_request import ValidationRequest
from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import (
    ExecuteValidationInput,
    ExecuteValidationUseCase,
)
from app.layer2_application.features.validation_rule.use_cases.generate_polar_use_case import (
    GeneratePolarInput,
    GeneratePolarUseCase,
)


async def validation_rule_node(state: dict[str, object], deps: dict[str, object]) -> dict[str, object]:
    request = ValidationRequest.from_state(state)
    if request.is_generate_polar():
        generate_polar = cast(GeneratePolarUseCase, deps["generate_polar_use_case"])
        response = await generate_polar.execute(GeneratePolarInput.from_params(request.message, request.params))
    else:
        execute_validation = cast(ExecuteValidationUseCase, deps["execute_validation_use_case"])
        response = await execute_validation.execute(ExecuteValidationInput.from_params(request.message, request.params))
    return response.to_dict()
