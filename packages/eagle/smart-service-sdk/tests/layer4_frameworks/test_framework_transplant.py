from smart_service_sdk.layer1_domain.entities.parsed_text_document import ParsedTextDocument
from smart_service_sdk.layer4_frameworks.config.app_config import Settings
from smart_service_sdk.layer4_frameworks.hana.sql_identifiers import (
    quote_identifier,
    validate_schema_identifier,
)
from smart_service_sdk.layer4_frameworks.repositories.json_repository_codec import JsonRepositoryCodec
from smart_service_sdk.layer4_frameworks.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from smart_service_sdk.layer4_frameworks.chunking.text_chunker import TextChunker


def test_hana_sql_identifier_validation() -> None:
    assert quote_identifier('A"B') == '"A""B"'
    assert validate_schema_identifier("EAGLE_01") == "EAGLE_01"

    try:
        validate_schema_identifier("bad-schema")
    except ValueError as exc:
        assert "HANA schema identifiers" in str(exc)
    else:
        raise AssertionError("invalid HANA schema identifier should fail")


def test_config_defaults_cover_rag_framework_settings() -> None:
    settings = Settings()

    assert settings.HANA_HOST
    assert settings.EMBEDDING_VECTOR_DIMENSIONS == 640
    assert settings.INGESTION_DEFAULT_CHUNK_SIZE_CHARACTERS > 0
    assert settings.OPENAI_MODEL
    assert settings.SPACY_MODEL_NAME == "en_core_web_sm"


def test_circuit_breaker_opens_and_recovers_after_reset() -> None:
    now = 0.0

    def current_time() -> float:
        return now

    breaker = CircuitBreaker(
        failure_threshold=2,
        reset_seconds=5,
        now_provider=current_time,
    )

    assert breaker.record_failure() is False
    assert breaker.record_failure() is True

    try:
        breaker.before_request()
    except CircuitBreakerOpenError:
        pass
    else:
        raise AssertionError("open circuit should reject requests")

    now = 6.0
    breaker.before_request()
    breaker.record_success()
    breaker.before_request()


def test_text_chunker_splits_and_preserves_parent_context() -> None:
    chunker = TextChunker(max_characters=12)
    document = ParsedTextDocument(
        content="Alpha beta\n\nGamma delta",
        content_format="text",
        metadata={"source": "unit"},
    )

    chunks = chunker.chunk(document, "doc-1")

    assert len(chunks) == 2
    assert chunks[0]["chunk_id"] == "doc-1-text-1"
    assert chunks[0]["metadata"]["source"] == "unit"
    assert chunks[0]["metadata"]["parent_context_id"] == chunks[1]["metadata"][
        "parent_context_id"
    ]


def test_json_repository_codec_dumps_compact_vector() -> None:
    codec = JsonRepositoryCodec()

    assert codec.dump_vector([1, "2.5", 3]) == "[1.0,2.5,3.0]"
    assert codec.load_json_object('{"a": 1}') == {"a": 1}
    assert codec.load_json_array("[1, 2]") == [1, 2]
