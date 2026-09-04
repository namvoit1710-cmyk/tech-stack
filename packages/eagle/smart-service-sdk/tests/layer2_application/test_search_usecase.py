import asyncio

from smart_service_sdk.layer1_domain.entities.duplicate_match_rule import DuplicateMatchRule
from smart_service_sdk.layer1_domain.entities.retrieval_candidate import (
    RetrievalCandidate,
)
from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    SearchRuntimeConfig,
)
from smart_service_sdk.layer2_application.features.search.use_cases.search_usecase import (
    SearchCommand,
    SearchItem,
    SearchUseCase,
)


class _ChunkRepository:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates
        self.exact_max_results: int | None = None
        self.fuzzy_max_results: int | None = None

    async def find_exact_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        normalized_probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        del tenant_id, probe_fields, filters
        self.exact_max_results = max_results
        matched: list[RetrievalCandidate] = []
        for candidate in self._candidates:
            search_rows = list(candidate.metadata.get("search_rows", []))
            exact_matches: list[str] = []
            for rule in rules:
                if rule.match_type != "exact":
                    continue
                field_name = str(rule.field)
                probe_value = normalized_probe_fields.get(field_name, "")
                if any(
                    str(row.get("normalized_fields", {}).get(field_name, "")) == probe_value
                    for row in search_rows
                ):
                    exact_matches.append(field_name)
            if exact_matches:
                matched.append(
                    RetrievalCandidate(
                        document_id=candidate.document_id,
                        chunk_id=candidate.chunk_id,
                        content=candidate.content,
                        score=0.0,
                        route=candidate.route,
                        metadata={**candidate.metadata, "exact_matches": exact_matches},
                    )
                )
        return matched

    async def find_fuzzy_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        expanded_terms: list[str],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        del tenant_id, filters
        self.fuzzy_max_results = max_results
        matched: list[RetrievalCandidate] = []
        for candidate in self._candidates:
            search_rows = list(candidate.metadata.get("search_rows", []))
            fuzzy_matches: dict[str, float] = {}
            matched_terms: list[str] = []
            for rule in rules:
                if rule.match_type != "fuzzy":
                    continue
                field_name = str(rule.field)
                probe_value = str(probe_fields.get(field_name, "")).strip().lower()
                row_value = next(
                    (
                        str(row.get("fields", {}).get(field_name, "")).strip().lower()
                        for row in search_rows
                        if str(row.get("fields", {}).get(field_name, "")).strip()
                    ),
                    "",
                )
                if not probe_value or not row_value:
                    continue
                candidate_terms = [probe_value, *expanded_terms]
                if any(term.strip().lower().startswith("wireless") for term in candidate_terms):
                    fuzzy_matches[field_name] = 0.92
                    matched_terms.append(probe_value)
            if fuzzy_matches:
                matched.append(
                    RetrievalCandidate(
                        document_id=candidate.document_id,
                        chunk_id=candidate.chunk_id,
                        content=candidate.content,
                        score=0.0,
                        route=candidate.route,
                        metadata={
                            **candidate.metadata,
                            "fuzzy_matches": fuzzy_matches,
                            "matched_terms": matched_terms,
                        },
                    )
                )
        return matched


class _RuntimeConfigurationService:
    async def get_search_config(self) -> SearchRuntimeConfig:
        return SearchRuntimeConfig(
            fuzzy_threshold=0.8,
            max_results=5,
            expand_terms_enabled=True,
        )


class _LlmService:
    async def expand_terms(self, text: str) -> list[str]:
        return [text, "wireless audio"]


def _candidate(
    document_id: str,
    chunk_id: str,
    content: str,
    *,
    search_rows: list[dict[str, object]] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=document_id,
        chunk_id=chunk_id,
        content=content,
        score=0.0,
        route=RetrievalRoute.STRUCTURED_TABLE,
        metadata={
            "chunk_role": "row_window",
            "search_rows": search_rows
            or [
                {
                    "row_number": 1,
                    "fields": {
                        "material name": "Finished Product: Wireless Earbuds",
                        "material description": "Wireless Earbuds",
                    },
                    "normalized_fields": {
                        "material name": "finished product: wireless earbuds",
                        "material description": "wireless earbuds",
                    },
                }
            ],
        },
    )


def test_search_usecase_matches_exact_and_fuzzy_rules_against_structured_rows() -> None:
    repository = _ChunkRepository(
        [
            _candidate(
                "DOC-1",
                "CHUNK-1",
                (
                    "## Sheet: Materials\n\n"
                    "Table anchor: Catalog\n"
                    "Rows 1-2 of 2\n\n"
                    "| Material Name | Material Description |\n"
                    "| --- | --- |\n"
                    "| Finished Product: Wireless Earbuds | Wireless Earbuds |\n"
                    "| Speaker Set | Portable Bluetooth Speaker |"
                ),
            )
        ]
    )
    use_case = SearchUseCase(
        llm_service=_LlmService(),
        retrieval_chunk_repository=repository,
        runtime_configuration_service=_RuntimeConfigurationService(),
        default_tenant_id="tenant-1",
    )

    result = asyncio.run(
        use_case.execute(
            SearchCommand(
                items=[
                    SearchItem(
                        key=" Material Name ",
                        value="finished product: wireless earbuds",
                        rule="exact",
                    ),
                    SearchItem(
                        key="Material Description",
                        value="wireless earbud",
                        rule="fuzzy",
                    ),
                ]
            )
        )
    )

    assert result.tenant_id == "tenant-1"
    assert result.candidates
    assert result.candidates[0].document_id == "DOC-1"
    assert result.candidates[0].metadata["exact_matches"] == ["material name"]
    assert "material description" in result.candidates[0].metadata["fuzzy_matches"]
    assert result.candidates[0].metadata["matched_rule_count"] == 2
    assert repository.exact_max_results == 5
    assert repository.fuzzy_max_results == 5


def test_search_usecase_returns_empty_when_no_rules_match() -> None:
    use_case = SearchUseCase(
        llm_service=_LlmService(),
        retrieval_chunk_repository=_ChunkRepository(
            [
                _candidate(
                    "DOC-2",
                    "CHUNK-2",
                    (
                        "## Sheet: Materials\n\n"
                        "Table anchor: Catalog\n"
                        "Rows 1-1 of 1\n\n"
                        "| Material Name |\n"
                        "| --- |\n"
                        "| Speaker Set |"
                    ),
                    search_rows=[
                        {
                            "row_number": 1,
                            "fields": {"material name": "Speaker Set"},
                            "normalized_fields": {"material name": "speaker set"},
                        }
                    ],
                )
            ]
        ),
        runtime_configuration_service=_RuntimeConfigurationService(),
        default_tenant_id="tenant-1",
    )

    result = asyncio.run(
        use_case.execute(
            SearchCommand(
                items=[
                    SearchItem(
                        key="Material Name",
                        value="wireless earbuds",
                        rule="exact",
                    )
                ]
            )
        )
    )

    assert result.tenant_id == "tenant-1"
    assert result.candidates == []
