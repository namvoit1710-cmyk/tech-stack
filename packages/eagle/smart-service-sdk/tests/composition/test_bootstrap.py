from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

import smart_service_sdk.bootstrap as bootstrap_module
from smart_service_sdk.layer4_frameworks.config.app_config import settings
from smart_service_sdk.layer4_frameworks.files import (
    FileServiceClient,
    LocalFileServiceClient,
)


def test_build_app_container_registers_core_usecases(built_container: dict) -> None:
    container = built_container
    assert "cleansing_enrichment_usecase" in container
    assert "rule_suggestion_usecase" in container
    assert "search_usecase" in container
    assert "similarity_usecase" in container
    assert "material_sds_analysis_usecase" in container
    assert "runtime_setting_repository" in container
    assert "get_search_config_usecase" in container
    assert "get_similarity_config_usecase" in container
    assert "database_backend" in container
    assert container["database_backend"] == "hana"
    assert "background_job_repository" in container
    assert "background_job_coordinator" in container
    assert "create_background_job_usecase" in container
    assert "get_background_job_usecase" in container
    assert "retrieval_chunk_repository" in container
    assert "retrieval_graph_repository" in container
    assert "governance_service" not in container
    assert "anomaly_detection_usecase" not in container
    assert "governance_guidance_usecase" not in container
    assert "get_configuration_usecase" not in container
    assert "update_configuration_usecase" not in container

    assert "duplicate_background_screening_usecase" not in container
    assert "batch_cleansing_enrichment_usecase" not in container
    assert "predictive_usecase" not in container


def test_auto_seed_data_defaults_to_false() -> None:
    assert settings.AUTO_SEED_DATA is False


def test_build_database_dependencies_requires_hana_settings(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_module.settings, "HANA_SCHEMA", "")
    monkeypatch.setattr(bootstrap_module.settings, "HANA_PASSWORD", "")

    with pytest.raises(
        ValueError,
        match="HANA-only bootstrap requires configured settings: HANA_SCHEMA, HANA_PASSWORD",
    ):
        bootstrap_module.build_app_container()


def test_build_file_service_client_uses_local_storage_when_base_url_missing(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_module.settings, "FILE_SERVICE_BASE_URL", "")
    monkeypatch.setattr(
        bootstrap_module.settings,
        "LOCAL_FILE_STORAGE_PATH",
        "./local-files",
    )

    client = bootstrap_module._build_file_service_client()

    assert isinstance(client, LocalFileServiceClient)


def test_build_file_service_client_uses_remote_client_when_base_url_present(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_module.settings,
        "FILE_SERVICE_BASE_URL",
        "http://files.example.test",
    )

    client = bootstrap_module._build_file_service_client()

    assert isinstance(client, FileServiceClient)


def test_build_app_container_loads_required_spacy_model(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeNlp:
        pipe_names = ["tok2vec", "tagger", "parser", "ner"]
        meta = {"name": "en_core_web_sm"}

    async def _fake_build_database_dependencies(logger, **kwargs):
        del logger, kwargs
        return {
            "database_backend": "hana",
            "embedding_provider": object(),
            "background_job_repository": object(),
            "retrieval_chunk_repository": object(),
            "retrieval_graph_repository": object(),
            "runtime_setting_repository": object(),
        }

    def _fake_load_spacy_model(model_name: str):
        calls.append(model_name)
        return _FakeNlp()

    monkeypatch.setattr(
        bootstrap_module,
        "_build_database_dependencies",
        _fake_build_database_dependencies,
    )
    monkeypatch.setattr(bootstrap_module.settings, "SPACY_MODEL_NAME", "en_core_web_sm")
    monkeypatch.setattr(bootstrap_module, "load_spacy_model", _fake_load_spacy_model)

    container = bootstrap_module.build_app_container()

    assert calls == ["en_core_web_sm"]
    assert "query_entity_extractor" in container


def test_build_app_container_fails_when_spacy_model_is_missing(monkeypatch) -> None:
    async def _fake_build_database_dependencies(logger, **kwargs):
        del logger, kwargs
        return {
            "database_backend": "hana",
            "embedding_provider": object(),
            "background_job_repository": object(),
            "retrieval_chunk_repository": object(),
            "retrieval_graph_repository": object(),
            "runtime_setting_repository": object(),
        }

    monkeypatch.setattr(
        bootstrap_module,
        "_build_database_dependencies",
        _fake_build_database_dependencies,
    )
    def _raise_missing_model(_: str):
        raise RuntimeError("missing spaCy model")

    monkeypatch.setattr(bootstrap_module, "load_spacy_model", _raise_missing_model)

    with pytest.raises(RuntimeError, match="missing spaCy model"):
        bootstrap_module.build_app_container()


def test_bootstrap_uses_deployment_sql_root_glob() -> None:
    bootstrap_source = Path(bootstrap_module.__file__).read_text(encoding="utf-8")

    assert 'deployment" / "sql").glob("*.sql")' in bootstrap_source
    assert 'deployment" / "sql" / "duplicate_check' not in bootstrap_source


def test_seed_runner_is_gated_by_auto_seed_data(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Cursor:
        def execute(self, sql: str) -> None:
            calls.append(("execute", sql))

        def close(self) -> None:
            calls.append(("close", None))

    class _Connection:
        def __init__(self) -> None:
            self.cursor_instance = _Cursor()

        def cursor(self) -> _Cursor:
            calls.append(("cursor", None))
            return self.cursor_instance

        def commit(self) -> None:
            calls.append(("commit", None))

        def rollback(self) -> None:
            calls.append(("rollback", None))

    class _Factory:
        def __init__(self) -> None:
            self.connection = _Connection()

        def acquire_without_schema(self) -> _Connection:
            calls.append(("acquire_without_schema", None))
            return self.connection

        def release(self, connection: _Connection) -> None:
            calls.append(("release", connection))

    monkeypatch.setattr(bootstrap_module.settings, "AUTO_SEED_DATA", False)
    bootstrap_module._run_hana_seed_sql_if_enabled(_Factory())
    assert calls == []


def test_seed_runner_noops_when_seed_folder_is_empty(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Factory:
        def acquire_without_schema(self):
            calls.append(("acquire_without_schema", None))
            raise AssertionError("seed runner should not acquire a connection without seed SQL")

        def release(self, connection) -> None:
            calls.append(("release", connection))

    monkeypatch.setattr(bootstrap_module.settings, "AUTO_SEED_DATA", True)
    bootstrap_module._run_hana_seed_sql_if_enabled(_Factory())
    assert calls == []
