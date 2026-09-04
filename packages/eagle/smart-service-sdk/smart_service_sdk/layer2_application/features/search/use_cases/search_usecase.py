from __future__ import annotations

from dataclasses import dataclass

from smart_service_sdk.layer1_domain.entities.duplicate_match_rule import DuplicateMatchRule
from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.interfaces.llm_service_interface import ILlmServiceClient
from smart_service_sdk.layer2_application.repositories.retrieval_chunk_repository_interface import (
    IRetrievalChunkRepository,
)


@dataclass
class SearchItem:
    key: str
    value: str
    rule: str


@dataclass
class SearchResult:
    tenant_id: str
    candidates: list[RetrievalCandidate]


class SearchUseCase:
    def __init__(
        self,
        *,
        llm_service: ILlmServiceClient,
        retrieval_chunk_repository: IRetrievalChunkRepository,
        runtime_configuration_service: RuntimeConfigurationService,
        default_tenant_id: str,
    ):
        self._llm_service = llm_service
        self._retrieval_chunk_repository = retrieval_chunk_repository
        self._runtime_configuration_service = runtime_configuration_service
        self._default_tenant_id = default_tenant_id

    async def execute(self, command: "SearchCommand") -> SearchResult:
        tenant_id = self._default_tenant_id
        config = await self._runtime_configuration_service.get_search_config()
        resolved_max_results = max(config.max_results, 1)
        fields = self._probe_fields(command.items)
        normalized_fields = {
            key: self._normalize(value)
            for key, value in fields.items()
            if self._normalize(value)
        }
        rules = self._resolved_rules(command.items, config.fuzzy_threshold)
        if not fields or not rules:
            return SearchResult(tenant_id=tenant_id, candidates=[])

        expanded_terms: list[str] = []
        if config.expand_terms_enabled:
            expanded_terms = await self._llm_service.expand_terms(
                " | ".join(
                    f"{field}: {value}"
                    for field, value in fields.items()
                    if str(value).strip()
                )
            )

        exact_matches = await self._retrieval_chunk_repository.find_exact_rule_matches(
            tenant_id=tenant_id,
            probe_fields=fields,
            normalized_probe_fields=normalized_fields,
            rules=rules,
            max_results=resolved_max_results,
            filters=None,
        )
        fuzzy_matches = await self._retrieval_chunk_repository.find_fuzzy_rule_matches(
            tenant_id=tenant_id,
            probe_fields=fields,
            rules=rules,
            expanded_terms=expanded_terms,
            max_results=resolved_max_results,
            filters=None,
        )

        return SearchResult(
            tenant_id=tenant_id,
            candidates=self._merge_matches(
                exact_matches=exact_matches,
                fuzzy_matches=fuzzy_matches,
                max_results=resolved_max_results,
                rule_count=len(rules),
            ),
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(str(value).strip().lower().split())

    @classmethod
    def _probe_fields(cls, items: list["SearchItem"]) -> dict[str, str]:
        fields: dict[str, str] = {}
        for item in items:
            normalized_key = cls._normalize(item.key)
            raw_value = str(item.value).strip()
            if not normalized_key or not raw_value:
                continue
            fields[normalized_key] = raw_value
        return fields

    @classmethod
    def _resolved_rules(
        cls,
        items: list["SearchItem"],
        fuzzy_threshold: float,
    ) -> list[DuplicateMatchRule]:
        rules: list[DuplicateMatchRule] = []
        for item in items:
            normalized_key = cls._normalize(item.key)
            normalized_rule = str(item.rule).strip().lower()
            normalized_value = str(item.value).strip()
            if not normalized_key or not normalized_value:
                continue
            if normalized_rule == "fuzzy":
                rules.append(
                    DuplicateMatchRule(
                        field=normalized_key,
                        match_type=normalized_rule,
                        threshold=fuzzy_threshold,
                    )
                )
                continue
            rules.append(
                DuplicateMatchRule(
                    field=normalized_key,
                    match_type="exact",
                )
            )
        return rules

    @classmethod
    def _merge_matches(
        cls,
        *,
        exact_matches: list[RetrievalCandidate],
        fuzzy_matches: list[RetrievalCandidate],
        max_results: int,
        rule_count: int,
    ) -> list[RetrievalCandidate]:
        merged: dict[tuple[str, str], RetrievalCandidate] = {}
        for candidate in [*exact_matches, *fuzzy_matches]:
            key = (candidate.document_id, candidate.chunk_id)
            metadata = dict(candidate.metadata)
            if key not in merged:
                merged[key] = RetrievalCandidate(
                    document_id=candidate.document_id,
                    chunk_id=candidate.chunk_id,
                    content=candidate.content,
                    score=0.0,
                    route=candidate.route,
                    metadata={
                        **metadata,
                        "exact_matches": list(metadata.get("exact_matches", [])),
                        "fuzzy_matches": dict(metadata.get("fuzzy_matches", {})),
                        "matched_terms": list(metadata.get("matched_terms", [])),
                    },
                )
                continue

            existing = merged[key]
            existing_metadata = dict(existing.metadata)
            exact_fields = list(existing_metadata.get("exact_matches", []))
            exact_fields.extend(metadata.get("exact_matches", []))
            fuzzy_scores = dict(existing_metadata.get("fuzzy_matches", {}))
            for field_name, score in dict(metadata.get("fuzzy_matches", {})).items():
                fuzzy_scores[str(field_name)] = max(
                    float(fuzzy_scores.get(str(field_name), 0.0)),
                    float(score),
                )
            matched_terms = list(existing_metadata.get("matched_terms", []))
            matched_terms.extend(metadata.get("matched_terms", []))
            merged[key] = RetrievalCandidate(
                document_id=existing.document_id,
                chunk_id=existing.chunk_id,
                content=existing.content,
                score=0.0,
                route=existing.route,
                metadata={
                    **existing_metadata,
                    "exact_matches": list(dict.fromkeys(exact_fields).keys()),
                    "fuzzy_matches": fuzzy_scores,
                    "matched_terms": list(dict.fromkeys(matched_terms).keys()),
                },
            )

        materialized: list[RetrievalCandidate] = []
        for candidate in merged.values():
            metadata = dict(candidate.metadata)
            exact_fields = list(metadata.get("exact_matches", []))
            fuzzy_scores = dict(metadata.get("fuzzy_matches", {}))
            matched_rule_count = len(set(exact_fields) | set(fuzzy_scores.keys()))
            total_score = float(len(exact_fields)) + sum(
                float(score) for score in fuzzy_scores.values()
            )
            materialized.append(
                RetrievalCandidate(
                    document_id=candidate.document_id,
                    chunk_id=candidate.chunk_id,
                    content=candidate.content,
                    score=round(total_score / max(rule_count, 1), 4),
                    route=candidate.route,
                    metadata={
                        **metadata,
                        "exact_matches": sorted(set(exact_fields)),
                        "fuzzy_matches": fuzzy_scores,
                        "matched_rule_count": matched_rule_count,
                        "requested_rule_count": rule_count,
                    },
                )
            )

        materialized.sort(
            key=lambda candidate: (
                float(candidate.score),
                int(candidate.metadata.get("matched_rule_count", 0)),
                candidate.document_id,
                candidate.chunk_id,
            ),
            reverse=True,
        )
        return materialized[:max_results]


@dataclass
class SearchCommand:
    items: list[SearchItem]
