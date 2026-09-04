import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from smart_service_sdk.layer2_application.features.background_job.background_job_coordinator import (
    BackgroundJobCoordinator,
)
from smart_service_sdk.layer2_application.features.background_job.use_cases.background_job_usecases import (
    CreateBackgroundJobUseCase,
    GetBackgroundJobUseCase,
)
from smart_service_sdk.layer2_application.features.cleansing_enrichment.use_cases.cleansing_enrichment_usecase import CleansingEnrichmentUseCase
from smart_service_sdk.layer2_application.features.background_job.background_job_runner import (
    BackgroundJobRunner,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_analysis_usecase import (
    MaterialSdsAnalysisUseCase,
)
from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_config_usecases import (
    GetMaterialSdsSourceConfigUseCase,
    UpdateMaterialSdsSourceConfigUseCase,
)
from smart_service_sdk.layer2_application.features.search.use_cases.search_config_usecases import (
    GetSearchConfigUseCase,
    UpdateSearchConfigUseCase,
)
from smart_service_sdk.layer2_application.features.search.use_cases.search_usecase import SearchUseCase
from smart_service_sdk.layer2_application.features.field_configuration.use_cases.field_config_usecases import (
    GetFieldConfigUseCase,
    UpdateFieldConfigUseCase,
)
from smart_service_sdk.layer2_application.features.field_configuration.use_cases.reindex_usecases import (
    ReindexFilesUseCase,
)
from smart_service_sdk.layer2_application.features.similarity.use_cases.similarity_config_usecases import (
    GetSimilarityConfigUseCase,
    UpdateSimilarityConfigUseCase,
)
from smart_service_sdk.layer2_application.features.similarity.use_cases.similarity_usecase import (
    SimilarityUseCase,
)
from smart_service_sdk.layer2_application.features.rule_suggestion.use_cases.rule_suggestion_usecase import RuleSuggestionUseCase
from smart_service_sdk.layer4_frameworks.config.app_config import settings
from smart_service_sdk.layer4_frameworks.embeddings.simple_embedding_provider import (
    SimpleEmbeddingProvider,
)
from smart_service_sdk.layer4_frameworks.files import (
    FileServiceClient,
    LocalFileServiceClient,
)
from smart_service_sdk.layer4_frameworks.hana import (
    HanaConnectionFactory,
    SEED_MIGRATIONS_TABLE_NAME,
    SchemaInitializer,
    execute_sql_paths,
    resolve_schema_sql_sources,
    resolve_seed_sql_sources,
)
from smart_service_sdk.layer4_frameworks.llm import (
    OpenAIGenerationClient,
    OpenAILlmServiceClient,
    StubGenerationClient,
    StubLlmServiceClient,
)
from smart_service_sdk.layer4_frameworks.logger.app_logger import AppLogger
from smart_service_sdk.layer4_frameworks.repositories.hana_background_job_repository import (
    HanaBackgroundJobRepository,
)
from smart_service_sdk.layer4_frameworks.repositories.hana_chunk_repository import HanaChunkRepository
from smart_service_sdk.layer4_frameworks.repositories.hana_graph_repository import HanaGraphRepository
from smart_service_sdk.layer4_frameworks.repositories.hana_runtime_setting_repository import (
    HanaRuntimeSettingRepository,
)
from smart_service_sdk.layer4_frameworks.repositories.in_memory_result_repository import InMemoryResultRepository
from smart_service_sdk.layer4_frameworks.retrieval import (
    QueryEntityExtractor,
    SpacyDependencyGraphExtractor,
)
from smart_service_sdk.layer4_frameworks.retrieval.spacy_model_loader import (
    load_spacy_model,
)


def _build_file_service_client():
    if not settings.FILE_SERVICE_BASE_URL.strip():
        return LocalFileServiceClient(settings.LOCAL_FILE_STORAGE_PATH)
    return FileServiceClient(
        base_url=settings.FILE_SERVICE_BASE_URL,
        timeout_seconds=settings.FILE_SERVICE_TIMEOUT_SECONDS,
    )


def _load_required_spacy_pipeline(logger: AppLogger):
    nlp = load_spacy_model(settings.SPACY_MODEL_NAME)
    model_name = str(getattr(nlp, "meta", {}).get("name") or settings.SPACY_MODEL_NAME)
    logger.info(
        "spaCy model ready",
        model_name=model_name,
        pipe_names=list(getattr(nlp, "pipe_names", ())),
    )
    return nlp


async def _build_hana_database(
    logger: AppLogger,
    *,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> dict:
    from smart_service_sdk.layer4_frameworks.embeddings.local_embedding_provider import (
        LocalEmbeddingProvider,
    )

    logger.info(
        "Initializing database backend",
        backend="hana",
        schema=settings.HANA_SCHEMA,
        host=settings.HANA_HOST,
        port=settings.HANA_PORT,
    )
    connection_factory = HanaConnectionFactory(
        host=settings.HANA_HOST,
        port=settings.HANA_PORT,
        user=settings.HANA_USER,
        password=settings.HANA_PASSWORD,
        schema=settings.HANA_SCHEMA,
        pool_size=settings.HANA_POOL_SIZE,
        pool_timeout_seconds=settings.HANA_POOL_TIMEOUT_SECONDS,
        statement_timeout_ms=settings.HANA_STATEMENT_TIMEOUT_MS,
        logger=logger,
    )
    schema_initializer = SchemaInitializer(
        connection_factory=connection_factory,
        sql_paths=resolve_schema_sql_sources(extra_schema_sql_paths),
        auto_create_schema=settings.AUTO_CREATE_SCHEMA,
        schema_name=settings.HANA_SCHEMA,
        graph_workspace_name="",
    )
    schema_initializer.initialize()
    _run_hana_seed_sql_if_enabled(
        connection_factory,
        extra_seed_sql_paths=extra_seed_sql_paths,
    )

    try:
        embedding_provider = LocalEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL_NAME,
            query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            max_input_characters=settings.INGESTION_MAX_EMBEDDING_INPUT_CHARACTERS,
        )
    except Exception:
        embedding_provider = SimpleEmbeddingProvider(settings.EMBEDDING_VECTOR_DIMENSIONS)

    return {
        "hana_connection_factory": connection_factory,
        "schema_initializer": schema_initializer,
        "embedding_provider": embedding_provider,
        "background_job_repository": HanaBackgroundJobRepository(connection_factory),
        "retrieval_chunk_repository": HanaChunkRepository(connection_factory),
        "retrieval_graph_repository": HanaGraphRepository(connection_factory),
        "runtime_setting_repository": HanaRuntimeSettingRepository(connection_factory),
    }


def _run_hana_seed_sql_if_enabled(
    connection_factory,
    *,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> None:
    if not settings.AUTO_SEED_DATA:
        return

    seed_paths = resolve_seed_sql_sources(extra_seed_sql_paths)
    if not seed_paths:
        return

    connection = connection_factory.acquire_without_schema()
    cursor = connection.cursor()
    try:
        execute_sql_paths(
            cursor,
            seed_paths,
            settings.HANA_SCHEMA,
            SEED_MIGRATIONS_TABLE_NAME,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection_factory.release(connection)


def _require_hana_settings() -> None:
    missing_settings: list[str] = []
    if not settings.HANA_SCHEMA.strip():
        missing_settings.append("HANA_SCHEMA")
    if not settings.HANA_PASSWORD.strip():
        missing_settings.append("HANA_PASSWORD")
    if missing_settings:
        missing_keys = ", ".join(missing_settings)
        raise ValueError(
            f"HANA-only bootstrap requires configured settings: {missing_keys}"
        )


async def _build_database_dependencies(
    logger: AppLogger,
    *,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> dict:
    _require_hana_settings()
    container = await _build_hana_database(
        logger,
        extra_schema_sql_paths=extra_schema_sql_paths,
        extra_seed_sql_paths=extra_seed_sql_paths,
    )
    container["database_backend"] = "hana"
    logger.info("Database backend ready", backend="hana")
    return container


async def build_app_container_async(
    *,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> dict:
    logger = AppLogger(
        name="ai_eagle",
        level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
    )
    logger.log(f"Bootstrapping {settings.APP_NAME}")

    generation_client = None
    use_stub_llm = bool(os.getenv("PYTEST_CURRENT_TEST"))
    if settings.OPENAI_API_KEY.strip() and not use_stub_llm:
        generation_client = OpenAIGenerationClient(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            base_url=settings.OPENAI_BASE_URL,
            timeout_seconds=settings.OPENAI_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_MAX_RETRIES,
            retry_backoff_seconds=settings.OPENAI_RETRY_BACKOFF_SECONDS,
        )
    elif use_stub_llm or not settings.OPENAI_API_KEY.strip():
        generation_client = StubGenerationClient()
    llm_service = (
        OpenAILlmServiceClient(generation_client)
        if generation_client is not None and not isinstance(generation_client, StubGenerationClient)
        else StubLlmServiceClient()
    )
    if use_stub_llm:
        logger.info("Using stub LLM client for pytest execution")
    result_repository = InMemoryResultRepository()
    database_dependencies = await _build_database_dependencies(
        logger,
        extra_schema_sql_paths=extra_schema_sql_paths,
        extra_seed_sql_paths=extra_seed_sql_paths,
    )
    logger.info(
        "Bootstrap database backend selected",
        backend=database_dependencies["database_backend"],
    )

    spacy_nlp = _load_required_spacy_pipeline(logger)
    query_entity_extractor = QueryEntityExtractor(
        spacy_model_name=settings.SPACY_MODEL_NAME,
        nlp=spacy_nlp,
        max_seed_count=settings.GRAPH_SEED_TOP_K,
    )
    graph_extractor = SpacyDependencyGraphExtractor(
        spacy_model_name=settings.SPACY_MODEL_NAME,
        nlp=spacy_nlp,
    )
    runtime_configuration_service = RuntimeConfigurationService(
        database_dependencies["runtime_setting_repository"]
    )
    file_service_client = _build_file_service_client()
    background_job_runner = BackgroundJobRunner(
        logger=logger,
        file_service_client=file_service_client,
        embedding_provider=database_dependencies["embedding_provider"],
        retrieval_chunk_repository=database_dependencies["retrieval_chunk_repository"],
        retrieval_graph_repository=database_dependencies["retrieval_graph_repository"],
        graph_extractor=graph_extractor,
        runtime_configuration_service=runtime_configuration_service,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )
    background_job_coordinator = BackgroundJobCoordinator(
        logger=logger,
        background_job_repository=database_dependencies["background_job_repository"],
        job_runner=background_job_runner,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )
    search_usecase = SearchUseCase(
        llm_service=llm_service,
        retrieval_chunk_repository=database_dependencies["retrieval_chunk_repository"],
        runtime_configuration_service=runtime_configuration_service,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )
    similarity_usecase = SimilarityUseCase(
        logger=logger,
        embedding_provider=database_dependencies["embedding_provider"],
        retrieval_chunk_repository=database_dependencies["retrieval_chunk_repository"],
        retrieval_graph_repository=database_dependencies["retrieval_graph_repository"],
        runtime_configuration_service=runtime_configuration_service,
        query_entity_extractor=query_entity_extractor,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )

    container = {
        "settings": settings,
        "logger": logger,
        "llm_service": llm_service,
        "generation_client": generation_client,
        "result_repository": result_repository,
        "query_entity_extractor": query_entity_extractor,
        "runtime_configuration_service": runtime_configuration_service,
        "database_backend": database_dependencies["database_backend"],
        "embedding_provider": database_dependencies["embedding_provider"],
        "background_job_repository": database_dependencies["background_job_repository"],
        "retrieval_chunk_repository": database_dependencies["retrieval_chunk_repository"],
        "retrieval_graph_repository": database_dependencies["retrieval_graph_repository"],
        "runtime_setting_repository": database_dependencies["runtime_setting_repository"],
        "file_service_client": file_service_client,
        "background_job_coordinator": background_job_coordinator,
        "create_background_job_usecase": CreateBackgroundJobUseCase(
            background_job_coordinator
        ),
        "get_background_job_usecase": GetBackgroundJobUseCase(
            background_job_coordinator
        ),
        "material_sds_analysis_usecase": MaterialSdsAnalysisUseCase(
            generation_client=generation_client,
            runtime_configuration_service=runtime_configuration_service,
        ),
        "get_material_sds_source_config_usecase": GetMaterialSdsSourceConfigUseCase(
            runtime_configuration_service
        ),
        "update_material_sds_source_config_usecase": UpdateMaterialSdsSourceConfigUseCase(
            runtime_configuration_service
        ),
        "search_usecase": search_usecase,
        "similarity_usecase": similarity_usecase,
        "cleansing_enrichment_usecase": CleansingEnrichmentUseCase(
            logger,
            llm_service,
            result_repository,
        ),
        "rule_suggestion_usecase": RuleSuggestionUseCase(
            logger,
            llm_service,
            result_repository,
        ),
        "get_search_config_usecase": GetSearchConfigUseCase(
            runtime_configuration_service
        ),
        "update_search_config_usecase": UpdateSearchConfigUseCase(
            runtime_configuration_service
        ),
        "get_similarity_config_usecase": GetSimilarityConfigUseCase(
            runtime_configuration_service
        ),
        "update_similarity_config_usecase": UpdateSimilarityConfigUseCase(
            runtime_configuration_service
        ),
        "background_job_runner": background_job_runner,
        "get_field_config_usecase": GetFieldConfigUseCase(
            runtime_configuration_service
        ),
        "update_field_config_usecase": UpdateFieldConfigUseCase(
            runtime_configuration_service
        ),
        "reindex_files_usecase": ReindexFilesUseCase(
            job_runner=background_job_runner,
            default_tenant_id=settings.DEFAULT_TENANT_ID,
        ),
    }
    if "hana_connection_factory" in database_dependencies:
        container["hana_connection_factory"] = database_dependencies["hana_connection_factory"]
        container["schema_initializer"] = database_dependencies["schema_initializer"]
    return container


def build_app_container(
    *,
    extra_schema_sql_paths: Sequence[str | Path] | None = None,
    extra_seed_sql_paths: Sequence[str | Path] | None = None,
) -> dict:
    return asyncio.run(
        build_app_container_async(
            extra_schema_sql_paths=extra_schema_sql_paths,
            extra_seed_sql_paths=extra_seed_sql_paths,
        )
    )
