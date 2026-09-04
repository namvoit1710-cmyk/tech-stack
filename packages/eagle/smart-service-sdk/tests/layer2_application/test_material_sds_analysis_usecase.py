import asyncio

import pytest

pytest.importorskip("fastapi")

from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_analysis_usecase import (
    MaterialSdsAnalysisCommand,
    MaterialSdsAnalysisItem,
    MaterialSdsAnalysisOptions,
    MaterialSdsAnalysisUseCase,
)
from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationResponse,
)


class _RecordingGenerationClient:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return GenerationResponse(output_text=self.output_text)


def _run(use_case, item):
    return asyncio.run(
        use_case.execute(MaterialSdsAnalysisCommand(materials=[item]))
    )


def test_deterministic_rule_shortcuts_without_calling_the_llm() -> None:
    # A solvent-based cleaner is a known hazardous category, so the deterministic
    # pre-screen decides "required" and the LLM is never called (Software 5.0 replay).
    client = _RecordingGenerationClient("{}")
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Industrial Cleaner",
            material_description="Solvent-based degreaser",
        ),
    )

    item = result.results[0]
    assert client.requests == []
    assert item.method == "rules"
    assert item.decision == "required"
    assert item.is_sds_required is True
    assert "solvents" in item.hazardous_categories
    assert any("Deterministic rule match" in e for e in item.evidence)


def test_non_hazardous_material_uses_plain_generation_request() -> None:
    client = _RecordingGenerationClient(
        """
        {
          "decision": "not_required",
          "hazardous_categories": [],
          "confidence": 0.88,
          "reasons": ["Inert mechanical fastener."],
          "evidence": ["No hazardous indicators in description."]
        }
        """
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Stainless Steel Hex Bolt",
            material_description="3/8 inch mechanical fastener",
        ),
    )

    item = result.results[0]
    assert len(client.requests) == 1
    assert client.requests[0].tools == ()
    assert client.requests[0].tool_choice is None
    assert item.method == "llm"
    assert item.decision == "not_required"
    assert item.is_sds_required is False


def test_new_fields_are_included_in_the_prompt() -> None:
    client = _RecordingGenerationClient(
        '{"decision":"needs_review","hazardous_categories":[],'
        '"confidence":0.4,"reasons":["ambiguous"],"evidence":["ambiguous"]}'
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Mounting Bracket",
            unspsc_code="31162800",
            manufacturer_name="Acme Fasteners",
            manufacturer_description="Powder-coated steel bracket",
        ),
    )

    prompt = client.requests[0].messages[0].content
    assert "31162800" in prompt
    assert "Acme Fasteners" in prompt
    assert "Powder-coated steel bracket" in prompt


def test_manufacturer_description_alone_is_enough_input() -> None:
    client = _RecordingGenerationClient(
        '{"decision":"not_required","hazardous_categories":[],'
        '"confidence":0.7,"reasons":["inert"],"evidence":["inert"]}'
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(manufacturer_description="Plastic widget, no coatings"),
    )

    item = result.results[0]
    assert item.method == "llm"
    assert len(client.requests) == 1


def test_web_search_defers_to_llm_and_requires_web_search_tool() -> None:
    client = _RecordingGenerationClient(
        """
        {
          "decision": "required",
          "hazardous_categories": ["batteries"],
          "confidence": 0.95,
          "reasons": ["Battery material usually requires SDS review."],
          "evidence": ["Web search and submitted text both indicate lithium battery handling."]
        }
        """
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Lithium Battery Pack",
            free_text_description="Rechargeable lithium-ion battery module",
            options=MaterialSdsAnalysisOptions(enable_online_search=True),
        ),
    )

    item = result.results[0]
    assert len(client.requests) == 1
    assert client.requests[0].tools == ({"type": "web_search"},)
    assert client.requests[0].tool_choice == "required"
    assert item.method == "llm"
    assert item.is_sds_required is True
    assert item.web_search_used is True
    assert item.web_search_status == "used"


def test_rule_match_floor_downgrades_false_not_required() -> None:
    # Online search requested => LLM path. Even if the model says not_required, a
    # deterministic rule hit must never yield a false negative.
    client = _RecordingGenerationClient(
        '{"decision":"not_required","hazardous_categories":[],'
        '"confidence":0.6,"reasons":["model saw nothing"],"evidence":["none"]}'
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Mystery Fluid",
            free_text_description="Drum contains acetone",
            options=MaterialSdsAnalysisOptions(enable_online_search=True),
        ),
    )

    item = result.results[0]
    assert item.decision == "needs_review"
    assert item.is_sds_required is False
    assert "solvents" in item.hazardous_categories
    assert any("Deterministic rule match" in e for e in item.evidence)


def test_returns_needs_review_for_blank_material() -> None:
    client = _RecordingGenerationClient("{}")
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(use_case, MaterialSdsAnalysisItem(material_type="raw_material"))

    item = result.results[0]
    assert client.requests == []
    assert item.method == "insufficient_input"
    assert item.decision == "needs_review"
    assert item.is_sds_required is False
    assert "Insufficient material details" in item.reasons[0]


def test_falls_back_to_needs_review_when_generation_fails() -> None:
    class _FailingGenerationClient:
        async def generate(self, request):
            del request
            raise RuntimeError("web search unavailable")

    use_case = MaterialSdsAnalysisUseCase(generation_client=_FailingGenerationClient())

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Unknown fluid",
            options=MaterialSdsAnalysisOptions(enable_online_search=True),
        ),
    )

    item = result.results[0]
    assert item.method == "error"
    assert item.decision == "needs_review"
    assert item.is_sds_required is False
    assert item.web_search_used is False
    assert item.web_search_status == "failed"
    assert "web search unavailable" in item.evidence[0]


def test_parses_fenced_json_output() -> None:
    client = _RecordingGenerationClient(
        """```json
{
  "decision": "not_required",
  "hazardous_categories": [],
  "confidence": 0.9,
  "reasons": [
    "Material is a semi-finished heating element.",
    "No hazardous materials or categories indicated."
  ],
  "evidence": [
    "Material description does not mention chemicals or hazardous substances.",
    "Free text description is blank, indicating no additional hazards."
  ]
}
```"""
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Semi-Finished: Heating Element",
            material_description="Heating Element - Semi-finished assembly",
        ),
    )

    item = result.results[0]
    assert item.method == "llm"
    assert item.decision == "not_required"
    assert item.is_sds_required is False
    assert item.confidence == 0.9
    assert item.reasons == [
        "Material is a semi-finished heating element.",
        "No hazardous materials or categories indicated.",
    ]


def test_llm_failure_falls_back_to_rule_decision_not_needs_review() -> None:
    # Online search requested => LLM path. If the model call fails, a known-hazard
    # material must NOT be downgraded to needs_review — the deterministic rule
    # signal is preserved and the decision stays required.
    class _FailingGenerationClient:
        async def generate(self, request):
            del request
            raise RuntimeError("model timeout")

    use_case = MaterialSdsAnalysisUseCase(generation_client=_FailingGenerationClient())

    result = _run(
        use_case,
        MaterialSdsAnalysisItem(
            material_name="Drum of acetone",
            options=MaterialSdsAnalysisOptions(enable_online_search=True),
        ),
    )

    item = result.results[0]
    assert item.method == "rules"
    assert item.decision == "required"
    assert item.is_sds_required is True
    assert "solvents" in item.hazardous_categories
    assert item.web_search_status == "failed"
    assert any("Deterministic rule match" in e for e in item.evidence)
    assert any("model timeout" in e for e in item.evidence)


def test_batch_preserves_order_and_per_item_method() -> None:
    client = _RecordingGenerationClient(
        '{"decision":"not_required","hazardous_categories":[],'
        '"confidence":0.8,"reasons":["inert"],"evidence":["inert"]}'
    )
    use_case = MaterialSdsAnalysisUseCase(generation_client=client)

    result = asyncio.run(
        use_case.execute(
            MaterialSdsAnalysisCommand(
                materials=[
                    MaterialSdsAnalysisItem(material_name="Acetone solvent drum"),  # rules
                    MaterialSdsAnalysisItem(material_type="raw_material"),           # insufficient
                    MaterialSdsAnalysisItem(material_name="Stainless steel washer"), # llm
                ]
            )
        )
    )

    assert [r.material_index for r in result.results] == [0, 1, 2]
    assert result.results[0].method == "rules"
    assert result.results[0].is_sds_required is True
    assert result.results[1].method == "insufficient_input"
    assert result.results[2].method == "llm"
    assert result.results[2].is_sds_required is False
    # Exactly one LLM call: only the third (non-hazard, sufficient) material.
    assert len(client.requests) == 1
