from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "smart_service_sdk"


def test_app_hana_files_do_not_reference_ae_duplicate_namespace() -> None:
    checked_files = [
        ROOT / "deployment" / "sql" / "001_create_initial_schema.sql",
        PACKAGE_ROOT / "layer4_frameworks" / "repositories" / "hana_chunk_repository.py",
        PACKAGE_ROOT / "layer4_frameworks" / "repositories" / "hana_graph_repository.py",
        PACKAGE_ROOT / "layer4_frameworks" / "hana" / "schema_initializer.py",
    ]

    for path in checked_files:
        assert "AE_DUPLICATE_" not in path.read_text(encoding="utf-8"), path


def test_initializer_uses_unified_ae_rag_tables() -> None:
    sql_path = ROOT / "deployment" / "sql" / "001_create_initial_schema.sql"
    sql = sql_path.read_text(encoding="utf-8")
    search_field_sql = (
        ROOT / "deployment" / "sql" / "003_create_chunk_search_fields.sql"
    ).read_text(encoding="utf-8")

    expected_table_names = [
        "AE_RAG_DOCUMENTS",
        "AE_RAG_CHUNKS",
        "AE_RAG_PARENT_CONTEXTS",
        "AE_RAG_GRAPH_ENTITIES",
        "AE_RAG_GRAPH_RELATIONS",
        "AE_RAG_GRAPH_MENTIONS",
        "AE_RAG_CHECK_RESULTS",
        "AE_RAG_BACKGROUND_JOBS",
        "AE_RAG_FILE_SYNC_STATE",
        "AE_RAG_HEADER_MAPPING_CACHE",
        "AE_RAG_CHUNK_SEARCH_FIELDS",
    ]

    for table_name in expected_table_names:
        assert table_name in (sql + search_field_sql)

    assert "NORMALIZED_NAME" in sql
    assert '"DOCUMENT_ID" NVARCHAR(64)' in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_ENTITIES" (', 1)[1].split(")';", 1)[0]
    assert '"RECORD_ID" NVARCHAR(128)' not in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_ENTITIES" (', 1)[1].split(")';", 1)[0]
    assert '"DOCUMENT_ID" NVARCHAR(64)' in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_RELATIONS" (', 1)[1].split(")';", 1)[0]
    assert '"RECORD_ID" NVARCHAR(128)' not in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_RELATIONS" (', 1)[1].split(")';", 1)[0]
    assert '"DOCUMENT_ID" NVARCHAR(64)' in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_MENTIONS" (', 1)[1].split(")';", 1)[0]
    assert '"RECORD_ID" NVARCHAR(128)' not in sql.split('CREATE COLUMN TABLE "AE_RAG_GRAPH_MENTIONS" (', 1)[1].split(")';", 1)[0]
    assert 'AE_IDX_RAG_GRAPH_ENTITIES_TENANT_DOC' not in sql
    assert 'AE_IDX_RAG_GRAPH_ENTITIES_TENANT_RECORD' not in sql
    assert 'AE_IDX_RAG_GRAPH_RELATIONS_TENANT_DOC' not in sql
    assert 'AE_IDX_RAG_GRAPH_RELATIONS_TENANT_RECORD' not in sql
    assert 'AE_IDX_RAG_GRAPH_MENTIONS_TENANT_DOC' not in sql
    assert 'AE_IDX_RAG_GRAPH_MENTIONS_TENANT_RECORD' not in sql
    assert 'AE_IDX_RAG_GRAPH_MENTIONS_ENTITY_RECORD' not in sql
    assert 'AE_IDX_RAG_SYNC_RUNS_STATUS' not in sql
    assert 'AE_IDX_RAG_BACKGROUND_JOBS_STATUS' in sql
    assert 'AE_RAG_RECORDS' not in sql
    assert 'AE_IDX_RAG_RECORDS_FILE' not in sql
    assert 'AE_IDX_RAG_RECORDS_FIELDS_JSON_FUZZY_SEARCH' not in sql
    assert 'CREATE VECTOR INDEX "AE_IDX_RAG_RECORDS_EMBEDDING"' not in sql
    assert 'CREATE VECTOR INDEX "AE_IDX_RAG_CHUNKS_EMBEDDING"' in sql
    assert '"CONTENT_JSON" NCLOB' not in sql
    assert 'AE_IDX_RAG_CHUNK_SEARCH_FIELDS_LOOKUP' in search_field_sql
    assert 'AE_IDX_RAG_CHUNK_SEARCH_FIELDS_DOC' in search_field_sql
    assert 'AE_IDX_RAG_CHUNK_SEARCH_FIELDS_FUZZY' in search_field_sql


def test_initializer_references_migration_ledger_tables() -> None:
    initializer_path = PACKAGE_ROOT / "layer4_frameworks" / "hana" / "schema_initializer.py"
    initializer_source = initializer_path.read_text(encoding="utf-8")

    assert "AE_SQL_MIGRATIONS" in initializer_source
    assert "AE_SEED_MIGRATIONS" in initializer_source


def test_runtime_configuration_sql_exists_and_seeds_defaults() -> None:
    sql_path = ROOT / "deployment" / "sql" / "002_create_service_configurations.sql"
    sql = sql_path.read_text(encoding="utf-8")

    assert "AE_SERVICE_CONFIGURATIONS" in sql
    assert '"KEY" NVARCHAR(255)' in sql
    assert '"VALUE" NCLOB NOT NULL' in sql
    assert "SEARCH_FUZZY_THRESHOLD" in sql
    assert "VECTOR_SEARCH_TOP_K" in sql
    assert "VECTOR_MIN_SCORE" in sql
    assert "SEARCH_MAX_RESULTS" in sql
    assert "DUPLICATE_FUZZY_THRESHOLD" not in sql
    assert "DUPLICATE_VECTOR_SEARCH_TOP_K" not in sql
    assert "DUPLICATE_VECTOR_MIN_SCORE" not in sql
