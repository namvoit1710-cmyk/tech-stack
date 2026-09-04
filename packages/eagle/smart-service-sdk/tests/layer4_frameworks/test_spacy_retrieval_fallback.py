import pytest

spacy = pytest.importorskip("spacy")

from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    GraphSourceChunk,
)
from smart_service_sdk.layer4_frameworks.retrieval.query_entity_extractor import (
    QueryEntityExtractor,
)
from smart_service_sdk.layer4_frameworks.retrieval.spacy_dependency_graph_extractor import (
    SpacyDependencyGraphExtractor,
)
from smart_service_sdk.layer4_frameworks.retrieval.spacy_model_loader import (
    load_spacy_model,
)


def test_spacy_loader_raises_when_models_are_missing() -> None:
    with pytest.raises(
        RuntimeError,
        match="Graph extraction requires an installed spaCy English model",
    ):
        load_spacy_model("missing_model_for_test")


def test_query_entity_extractor_falls_back_to_simple_token_candidates() -> None:
    extractor = QueryEntityExtractor(spacy_model_name="missing_model_for_test")

    seed_texts = extractor.extract("Acme Industrial Company in Bangkok")

    assert seed_texts
    assert "acme industrial company bangkok" in {
        seed.lower() for seed in seed_texts
    }


def test_graph_extractor_raises_when_spacy_model_is_missing() -> None:
    extractor = SpacyDependencyGraphExtractor(spacy_model_name="missing_model_for_test")

    with pytest.raises(
        RuntimeError,
        match="Graph extraction requires an installed spaCy English model",
    ):
        extractor.extract(
            document_id="doc-1",
            chunks=[GraphSourceChunk(chunk_id="chunk-1", text="Acme acquired Contoso.")],
        )


def test_graph_extractor_uses_supplied_nlp_without_fallback() -> None:
    extractor = SpacyDependencyGraphExtractor(nlp=spacy.blank("en"))

    result = extractor.extract(
        document_id="doc-1",
        chunks=[GraphSourceChunk(chunk_id="chunk-1", text="Acme acquired Contoso.")],
    )

    assert result == extractor.extract(
        document_id="doc-1",
        chunks=[GraphSourceChunk(chunk_id="chunk-1", text="Acme acquired Contoso.")],
    )


def test_graph_extractor_parses_structured_field_value_chunks() -> None:
    extractor = SpacyDependencyGraphExtractor(nlp=spacy.blank("en"))

    result = extractor.extract(
        document_id="doc-1",
        chunks=[
            GraphSourceChunk(
                chunk_id="chunk-1",
                text=(
                    "BP Category: Organization | City: Chicago | Country: US | "
                    "Language: EN | Organization Name: Accenture Technology Solutions Inc."
                ),
            )
        ],
    )

    entities = {(entity.canonical_name, entity.entity_type) for entity in result.entities}
    assert ("Organization", "BP Category") in entities
    assert ("Chicago", "City") in entities
    assert ("US", "Country") in entities
    assert (
        "Accenture Technology Solutions Inc.",
        "Organization Name",
    ) in entities
    assert result.relations == ()
    assert {mention.surface_text for mention in result.mentions} >= {
        "Organization",
        "Chicago",
        "US",
        "EN",
        "Accenture Technology Solutions Inc.",
    }


def test_graph_extractor_structured_mentions_use_value_offsets() -> None:
    extractor = SpacyDependencyGraphExtractor(nlp=spacy.blank("en"))
    text = "City: Chicago | Country: US"

    result = extractor.extract(
        document_id="doc-1",
        chunks=[GraphSourceChunk(chunk_id="chunk-1", text=text)],
    )

    mentions_by_text = {mention.surface_text: mention for mention in result.mentions}
    chicago = mentions_by_text["Chicago"]
    us = mentions_by_text["US"]
    assert text[chicago.start_offset:chicago.end_offset] == "Chicago"
    assert text[us.start_offset:us.end_offset] == "US"


def test_graph_extractor_structured_dedupes_by_type_and_value() -> None:
    extractor = SpacyDependencyGraphExtractor(nlp=spacy.blank("en"))

    result = extractor.extract(
        document_id="doc-1",
        chunks=[
            GraphSourceChunk(
                chunk_id="chunk-1",
                text="City: Chicago | Organization Name: Chicago",
            )
        ],
    )

    entities = {(entity.canonical_name, entity.entity_type) for entity in result.entities}
    assert ("Chicago", "City") in entities
    assert ("Chicago", "Organization Name") in entities
    assert len(result.entities) == 2


def test_graph_extractor_skips_malformed_structured_segments() -> None:
    extractor = SpacyDependencyGraphExtractor(nlp=spacy.blank("en"))

    result = extractor.extract(
        document_id="doc-1",
        chunks=[
            GraphSourceChunk(
                chunk_id="chunk-1",
                text=" | Missing Colon | City: Chicago | : bad | Country:   ",
            )
        ],
    )

    assert {(entity.canonical_name, entity.entity_type) for entity in result.entities} == {
        ("Chicago", "City")
    }
    assert [mention.surface_text for mention in result.mentions] == ["Chicago"]


def test_graph_extractor_preserves_non_structured_spacy_path() -> None:
    nlp = spacy.blank("en")
    ruler = nlp.add_pipe("entity_ruler")
    ruler.add_patterns(
        [
            {"label": "ORG", "pattern": "Acme"},
            {"label": "ORG", "pattern": "Contoso"},
        ]
    )
    extractor = SpacyDependencyGraphExtractor(nlp=nlp)

    result = extractor.extract(
        document_id="doc-1",
        chunks=[GraphSourceChunk(chunk_id="chunk-1", text="Acme acquired Contoso.")],
    )

    entities = {(entity.canonical_name, entity.entity_type) for entity in result.entities}
    assert ("Acme", "ORG") in entities
    assert ("Contoso", "ORG") in entities
