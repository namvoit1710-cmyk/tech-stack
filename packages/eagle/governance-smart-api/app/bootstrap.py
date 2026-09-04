from app.layer2_application.features.import_data.use_cases.import_data_usecase import (
    ImportDataUseCase,
)
from app.layer2_application.features.import_data.use_cases.upload_import_file_usecase import (
    UploadImportFileUseCase,
)
from smart_service_sdk.layer2_application.features.background_job.use_cases.background_job_usecases import (
    CreateBackgroundJobUseCase,
)
from app.layer4_frameworks.logger.app_logger import AppLogger
from app.layer4_frameworks.clients.in_process_import_job_submitter import (
    InProcessImportJobSubmitter,
)


def build_governance_dependencies(shared_container: dict) -> dict:
    create_import_job_usecase = shared_container.get(
        "create_background_job_usecase"
    )
    if create_import_job_usecase is None or not hasattr(
        create_import_job_usecase, "execute"
    ):
        raise KeyError("create_background_job_usecase")
    shared_result_repository = shared_container.get("duplicate_result_repository")
    
    shared_settings = shared_container.get("settings")
    logger = AppLogger(
        name="governance_smart_api",
        level=str(getattr(shared_settings, "LOG_LEVEL", "INFO"))
        if shared_settings is not None
        else "INFO",
        log_format=str(getattr(shared_settings, "LOG_FORMAT", "text"))
        if shared_settings is not None
        else "text",
    )
    import_job_submitter = InProcessImportJobSubmitter(create_import_job_usecase)
    import_data_usecase = ImportDataUseCase(
        import_job_submitter=import_job_submitter
    )
    upload_import_file_usecase = UploadImportFileUseCase(
        upload_result_repository=shared_container["result_repository"],
        embedding_provider=shared_container["embedding_provider"],
        retrieval_chunk_repository=shared_container["retrieval_chunk_repository"],
        runtime_configuration_service=shared_container["runtime_configuration_service"],
        default_tenant_id=str(getattr(shared_settings, "DEFAULT_TENANT_ID", "")).strip(),
    )
    return {
        "logger": logger,
        "import_job_submitter": import_job_submitter,
        "import_data_usecase": import_data_usecase,
        "upload_import_file_usecase": upload_import_file_usecase,
    }
