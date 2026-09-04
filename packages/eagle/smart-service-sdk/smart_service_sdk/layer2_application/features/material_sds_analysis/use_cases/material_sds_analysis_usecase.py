from __future__ import annotations

import json
from dataclasses import dataclass, field

from smart_service_sdk.layer2_application.features.material_sds_analysis.material_sds_rules import (
    RuleMatch,
    screen_text,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    MaterialSdsSourceConfig,
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationMessage,
    GenerationRequest,
    IGenerationClient,
)


@dataclass(frozen=True)
class MaterialSdsAnalysisOptions:
    enable_online_search: bool = False


@dataclass(frozen=True)
class MaterialSdsAnalysisItem:
    material_name: str = ""
    material_type: str = ""
    material_group: str = ""
    material_description: str = ""
    free_text_description: str = ""
    unspsc_code: str = ""
    classification: str = ""
    manufacturer_name: str = ""
    manufacturer_description: str = ""
    options: MaterialSdsAnalysisOptions = field(
        default_factory=MaterialSdsAnalysisOptions
    )


@dataclass(frozen=True)
class MaterialSdsAnalysisCommand:
    materials: list[MaterialSdsAnalysisItem]


@dataclass(frozen=True)
class MaterialSdsAnalysisItemResult:
    material_index: int
    decision: str
    is_sds_required: bool
    hazardous_categories: list[str]
    confidence: float
    reasons: list[str]
    evidence: list[str]
    web_search_used: bool
    web_search_status: str
    method: str


@dataclass(frozen=True)
class ParsedMaterialSdsOutput:
    decision: str
    hazardous_categories: list[str]
    confidence: float
    reasons: list[str]
    evidence: list[str]


@dataclass(frozen=True)
class MaterialSdsAnalysisResult:
    results: list[MaterialSdsAnalysisItemResult]


class MaterialSdsAnalysisUseCase:
    _ALLOWED_DECISIONS = {"required", "not_required", "needs_review"}
    _WEB_SEARCH_TOOL = {"type": "web_search"}
    _WEB_SEARCH_TOOL_CHOICE = "required"
    _RULES_CONFIDENCE = 0.9
    _HAZARDOUS_CATEGORY_HINTS = (
        "chemicals",
        "solvents",
        "fuels",
        "lubricants",
        "pesticides",
        "cleaning_agents",
        "corrosives",
        "flammables",
        "gases",
        "aerosols",
        "batteries",
        "oxidizers",
        "toxins",
        "compressed_gases",
    )

    def __init__(
        self,
        generation_client: IGenerationClient,
        runtime_configuration_service: RuntimeConfigurationService | None = None,
    ):
        self._generation_client = generation_client
        self._runtime_configuration_service = runtime_configuration_service

    async def _resolve_source_config(self) -> MaterialSdsSourceConfig:
        if self._runtime_configuration_service is None:
            return MaterialSdsSourceConfig(resource_urls=[], allowed_domains=[])
        return await self._runtime_configuration_service.get_material_sds_source_config()

    async def execute(
        self, command: MaterialSdsAnalysisCommand
    ) -> MaterialSdsAnalysisResult:
        source_config = await self._resolve_source_config()
        resource_urls = list(source_config.resource_urls)
        allowed_domains = list(source_config.allowed_domains)
        results: list[MaterialSdsAnalysisItemResult] = []
        for index, item in enumerate(command.materials):
            results.append(
                await self._analyze_item(
                    index,
                    item,
                    resource_urls=resource_urls,
                    allowed_domains=allowed_domains,
                )
            )
        return MaterialSdsAnalysisResult(results=results)

    async def _analyze_item(
        self,
        index: int,
        item: MaterialSdsAnalysisItem,
        *,
        resource_urls: list[str],
        allowed_domains: list[str],
    ) -> MaterialSdsAnalysisItemResult:
        if not self._has_minimum_input(item):
            return MaterialSdsAnalysisItemResult(
                material_index=index,
                decision="needs_review",
                is_sds_required=False,
                hazardous_categories=[],
                confidence=0.0,
                reasons=[
                    "Insufficient material details. Provide material_name, "
                    "material_description, free_text_description, or manufacturer details."
                ],
                evidence=["No analyzable material text was provided."],
                web_search_used=False,
                web_search_status="not_requested",
                method="insufficient_input",
            )

        rule_matches = screen_text(self._analyzable_text(item))
        use_web_search = bool(item.options.enable_online_search)

        # Software 5.0 recipe: a confident deterministic hit replays the decision
        # without an LLM call — UNLESS the caller explicitly asked for a fresh
        # online-grounded answer, in which case we defer to the model path.
        if rule_matches and not use_web_search:
            return self._rule_based_result(index, rule_matches)

        return await self._llm_based_result(
            index,
            item,
            rule_matches=rule_matches,
            use_web_search=use_web_search,
            resource_urls=resource_urls,
            allowed_domains=allowed_domains,
        )

    def _rule_based_result(
        self, index: int, rule_matches: list[RuleMatch]
    ) -> MaterialSdsAnalysisItemResult:
        categories = [match.category for match in rule_matches]
        return MaterialSdsAnalysisItemResult(
            material_index=index,
            decision="required",
            is_sds_required=True,
            hazardous_categories=categories,
            confidence=self._RULES_CONFIDENCE,
            reasons=[
                "SDS required by deterministic hazardous-category rule pre-screen."
            ],
            evidence=[self._rule_evidence(match) for match in rule_matches],
            web_search_used=False,
            web_search_status="not_requested",
            method="rules",
        )

    async def _llm_based_result(
        self,
        index: int,
        item: MaterialSdsAnalysisItem,
        *,
        rule_matches: list[RuleMatch],
        use_web_search: bool,
        resource_urls: list[str],
        allowed_domains: list[str],
    ) -> MaterialSdsAnalysisItemResult:
        try:
            response = await self._generation_client.generate(
                self._build_request(
                    item,
                    use_web_search=use_web_search,
                    resource_urls=resource_urls,
                    allowed_domains=allowed_domains,
                )
            )
            parsed = self._parse_output(response.output_text)
        except Exception as exc:
            failure = f"Analysis failure: {str(exc).strip() or exc.__class__.__name__}"
            # If the model call fails but a deterministic rule already matched, do
            # not discard that confident hazard signal — fall back to the rule
            # decision rather than downgrading a known hazard to needs_review.
            if rule_matches:
                return MaterialSdsAnalysisItemResult(
                    material_index=index,
                    decision="required",
                    is_sds_required=True,
                    hazardous_categories=[match.category for match in rule_matches],
                    confidence=self._RULES_CONFIDENCE,
                    reasons=[
                        "Model call failed; fell back to the deterministic hazardous-category rule pre-screen."
                    ],
                    evidence=[self._rule_evidence(match) for match in rule_matches]
                    + [failure],
                    web_search_used=False,
                    web_search_status="failed" if use_web_search else "not_requested",
                    method="rules",
                )
            return MaterialSdsAnalysisItemResult(
                material_index=index,
                decision="needs_review",
                is_sds_required=False,
                hazardous_categories=[],
                confidence=0.0,
                reasons=["SDS analysis could not be completed automatically."],
                evidence=[failure],
                web_search_used=False,
                web_search_status="failed" if use_web_search else "not_requested",
                method="error",
            )

        decision = parsed.decision
        reasons = list(parsed.reasons)
        evidence = self._augment_evidence(
            list(parsed.evidence),
            use_web_search=use_web_search,
            resource_urls=resource_urls,
            allowed_domains=allowed_domains,
        )
        categories = list(parsed.hazardous_categories)

        # Safety floor: a deterministic rule hit must never be overruled into a
        # false "not_required". Downgrade the model to needs_review and keep the
        # rule evidence so the reviewer sees the conflict.
        if rule_matches:
            for match in rule_matches:
                if match.category not in categories:
                    categories.append(match.category)
            evidence.extend(self._rule_evidence(match) for match in rule_matches)
            if decision == "not_required":
                decision = "needs_review"
                reasons.append(
                    "Model returned not_required but a deterministic hazardous-category "
                    "rule matched; downgraded to needs_review for human confirmation."
                )

        return MaterialSdsAnalysisItemResult(
            material_index=index,
            decision=decision,
            is_sds_required=decision == "required",
            hazardous_categories=categories,
            confidence=parsed.confidence,
            reasons=reasons,
            evidence=evidence,
            web_search_used=use_web_search,
            web_search_status="used" if use_web_search else "not_requested",
            method="llm",
        )

    @staticmethod
    def _rule_evidence(match: RuleMatch) -> str:
        return (
            f"Deterministic rule match: hazardous category '{match.category}' "
            f"(matched term: '{match.matched_term}')."
        )

    @classmethod
    def _analyzable_text(cls, item: MaterialSdsAnalysisItem) -> str:
        return " ".join(
            cls._normalize(value)
            for value in (
                item.material_name,
                item.material_type,
                item.material_group,
                item.material_description,
                item.free_text_description,
                item.unspsc_code,
                item.classification,
                item.manufacturer_name,
                item.manufacturer_description,
            )
            if cls._normalize(value)
        )

    @classmethod
    def _has_minimum_input(cls, item: MaterialSdsAnalysisItem) -> bool:
        return any(
            cls._normalize(value)
            for value in (
                item.material_name,
                item.material_description,
                item.free_text_description,
                item.manufacturer_name,
                item.manufacturer_description,
            )
        )

    @classmethod
    def _normalize(cls, value: str) -> str:
        return " ".join(str(value).split()).strip()

    @classmethod
    def _build_prompt(
        cls,
        item: MaterialSdsAnalysisItem,
        resource_urls: list[str] | None = None,
    ) -> str:
        references_block = ""
        if resource_urls:
            references_block = (
                "\nAuthoritative reference sources (prefer these over the open web; "
                "cite any you rely on in evidence):\n"
                + "\n".join(f"- {url}" for url in resource_urls)
                + "\n"
            )
        return (
            "You are performing a Safety Data Sheet (SDS) requirement screening for a "
            "new material before creation.\n"
            "Determine whether SDS is required, not required, or needs review.\n"
            "Consider all provided fields: material type, material group, material name, "
            "material description, free-text description, UNSPSC/classification code, and "
            "manufacturer name/description.\n"
            "Look for hazardous or safety-regulated categories including: "
            + ", ".join(cls._HAZARDOUS_CATEGORY_HINTS)
            + ".\n"
            "If signals are ambiguous, missing, or weak, return needs_review.\n"
            "Return compact JSON with keys decision, hazardous_categories, confidence, reasons, evidence.\n"
            "decision must be one of required, not_required, needs_review.\n"
            "hazardous_categories must be a JSON array of snake_case strings.\n"
            "confidence must be a number between 0 and 1.\n"
            "reasons and evidence must each be a JSON array of short strings.\n"
            + references_block
            + "\n"
            f"material_name: {cls._normalize(item.material_name) or '(blank)'}\n"
            f"material_type: {cls._normalize(item.material_type) or '(blank)'}\n"
            f"material_group: {cls._normalize(item.material_group) or '(blank)'}\n"
            f"material_description: {cls._normalize(item.material_description) or '(blank)'}\n"
            f"free_text_description: {cls._normalize(item.free_text_description) or '(blank)'}\n"
            f"unspsc_code: {cls._normalize(item.unspsc_code) or '(blank)'}\n"
            f"classification: {cls._normalize(item.classification) or '(blank)'}\n"
            f"manufacturer_name: {cls._normalize(item.manufacturer_name) or '(blank)'}\n"
            f"manufacturer_description: {cls._normalize(item.manufacturer_description) or '(blank)'}"
        )

    @classmethod
    def _build_request(
        cls,
        item: MaterialSdsAnalysisItem,
        *,
        use_web_search: bool,
        resource_urls: list[str] | None = None,
        allowed_domains: list[str] | None = None,
    ) -> GenerationRequest:
        tools: tuple[object, ...] = ()
        if use_web_search:
            tools = (cls._build_web_search_tool(allowed_domains),)
        return GenerationRequest(
            messages=(
                GenerationMessage(
                    role="user", content=cls._build_prompt(item, resource_urls)
                ),
            ),
            temperature=0.0,
            max_output_tokens=500,
            response_format="json_object",
            tools=tools,
            tool_choice=cls._WEB_SEARCH_TOOL_CHOICE if use_web_search else None,
        )

    @classmethod
    def _build_web_search_tool(cls, allowed_domains: list[str] | None) -> dict:
        # Restrict the online lookup to the configured allow-list when present;
        # otherwise fall back to the open-web web_search tool (SA-1474 AC3).
        if allowed_domains:
            return {
                "type": "web_search",
                "filters": {"allowed_domains": list(allowed_domains)},
            }
        return dict(cls._WEB_SEARCH_TOOL)

    @classmethod
    def _augment_evidence(
        cls,
        evidence: list[str],
        *,
        use_web_search: bool,
        resource_urls: list[str],
        allowed_domains: list[str],
    ) -> list[str]:
        # Reflect the configured sources actually applied to this analysis so the
        # response evidence shows what the lookup was pointed at (SA-1474 AC3/AC4).
        augmented = list(evidence)
        if resource_urls:
            augmented.append(
                "Authoritative references provided: " + ", ".join(resource_urls) + "."
            )
        if use_web_search and allowed_domains:
            augmented.append(
                "Online lookup restricted to allowed domains: "
                + ", ".join(allowed_domains)
                + "."
            )
        return augmented

    @classmethod
    def _parse_output(cls, output_text: str) -> ParsedMaterialSdsOutput:
        try:
            payload = json.loads(cls._strip_code_fences(output_text))
        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned malformed JSON for SDS analysis") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM SDS analysis output must be a JSON object")

        decision = cls._normalize(payload.get("decision", ""))
        if decision not in cls._ALLOWED_DECISIONS:
            raise ValueError("LLM SDS analysis output contained an invalid decision")

        hazardous_categories = cls._string_list(payload.get("hazardous_categories", []))
        reasons = cls._string_list(payload.get("reasons", []))
        evidence = cls._string_list(payload.get("evidence", []))
        confidence = cls._coerce_confidence(payload.get("confidence", 0.0))
        if not reasons:
            reasons = ["The model did not provide a reasoned SDS decision."]
        if not evidence:
            evidence = ["No supporting evidence was returned by the model."]
        return ParsedMaterialSdsOutput(
            decision=decision,
            hazardous_categories=hazardous_categories,
            confidence=confidence,
            reasons=reasons,
            evidence=evidence,
        )

    @classmethod
    def _strip_code_fences(cls, output_text: str) -> str:
        normalized = str(output_text).strip()
        if not normalized.startswith("```"):
            return normalized

        lines = normalized.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @classmethod
    def _string_list(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            normalized = cls._normalize(item)
            if normalized:
                items.append(normalized)
        return items

    @staticmethod
    def _coerce_confidence(value: object) -> float:
        if not isinstance(value, (int, float, str)):
            return 0.0
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return round(min(max(confidence, 0.0), 1.0), 4)
