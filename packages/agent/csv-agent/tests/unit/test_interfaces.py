from typing import Any

from app.layer2_application.interfaces.csv_json_gateway import (
    CsvJsonGatewayError,
    ICsvJsonGateway,
)
from app.layer2_application.interfaces.data_factory_gateway import IDataFactoryGateway
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway
from app.layer2_application.interfaces.file_upload_gateway import (
    FileUploadGatewayError,
    IFileUploadGateway,
)
from app.layer2_application.interfaces.llm_service import ILLMService
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)
from app.layer2_application.interfaces.workflow_node_gateway import IWorkflowNodeGateway


def test_layer2_interfaces_are_protocol_only_exports():
    assert ICsvJsonGateway is not None
    assert IDataFactoryGateway is not None
    assert IFilePreviewGateway is not None
    assert IFileUploadGateway is not None
    assert ILLMService is not None
    assert IRuntimeSettingsProvider is not None
    assert IStructuredLLMGateway is not None
    assert IWorkflowNodeGateway is not None


def test_gateway_error_types_keep_safe_fields():
    csv_error = CsvJsonGatewayError("CODE", "message", "details", 409)
    upload_error = FileUploadGatewayError("UP", "upload", "safe", 503)

    assert csv_error.code == "CODE"
    assert csv_error.status_code == 409
    assert upload_error.code == "UP"
    assert upload_error.status_code == 503


def test_no_service_locator_shims_are_exported():
    import app.layer2_application.interfaces.csv_json_gateway as csv_json_gateway
    import app.layer2_application.interfaces.data_factory_gateway as data_factory_gateway
    import app.layer2_application.interfaces.file_preview_gateway as file_preview_gateway
    import app.layer2_application.interfaces.file_upload_gateway as file_upload_gateway
    import app.layer2_application.interfaces.llm_service as llm_service
    import app.layer2_application.interfaces.settings_provider as settings_provider
    import app.layer2_application.interfaces.structured_llm_gateway as structured_llm_gateway
    import app.layer2_application.interfaces.workflow_node_gateway as workflow_node_gateway

    modules: list[Any] = [
        csv_json_gateway,
        data_factory_gateway,
        file_preview_gateway,
        file_upload_gateway,
        llm_service,
        settings_provider,
        structured_llm_gateway,
        workflow_node_gateway,
    ]
    for module in modules:
        exported = set(dir(module))
        assert not any(name.startswith("configure_") for name in exported)
        assert not any(
            name.startswith("get_")
            and name.endswith(("gateway", "service", "provider"))
            for name in exported
        )
