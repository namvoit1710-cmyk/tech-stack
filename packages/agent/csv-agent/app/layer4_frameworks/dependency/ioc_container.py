from __future__ import annotations

import logging
from typing import Any

from agent_sdk import ILLMService
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

from app.layer2_application.interfaces.data_factory_gateway import IDataFactoryGateway
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)
from app.layer2_application.use_cases.plan_usecase import PlanUseCase
from app.layer2_application.use_cases.review_usecase import ReviewUseCase
from app.layer4_frameworks.config.settings_adapter import settings
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)
from app.layer4_frameworks.providers.csv_json_gateway_provider import (
    CsvJsonGatewayProvider,
)
from app.layer4_frameworks.providers.file_preview_gateway_provider import (
    FilePreviewGatewayProvider,
)
from app.layer4_frameworks.providers.file_upload_gateway_provider import (
    FileUploadGatewayProvider,
)
from app.layer4_frameworks.providers.mcp_data_factory_gateway_provider import (
    MCPDataFactoryGatewayProvider,
)
from app.layer4_frameworks.providers.runtime_settings_provider import (
    RuntimeSettingsProvider,
)
from app.layer4_frameworks.providers.structured_llm_gateway_provider import (
    SDKStructuredLLMGatewayProvider,
)
from app.layer4_frameworks.providers.workflow_node_gateway_provider import (
    WorkflowNodeGatewayProvider,
)
from app.layer4_frameworks.tools.convert_csv_to_json import (
    make_convert_csv_to_json_tool,
)
from app.layer4_frameworks.tools.convert_json_to_csv import (
    make_convert_json_to_csv_tool,
)
from app.layer4_frameworks.tools.create_file import make_create_file_tool
from app.layer4_frameworks.tools.get_first_n_rows import make_get_first_n_rows_tool
from app.layer4_frameworks.tools.import_file_to_node import (
    make_import_file_to_node_tool,
)
from app.layer4_frameworks.tools.minus import minus
from app.layer4_frameworks.tools.plus import plus
from app.layer4_frameworks.tools.query_file_data import make_query_file_data_tool
from app.layer4_frameworks.tools.revert_file import make_revert_file_tool
from app.layer4_frameworks.tools.transform_column import make_transform_column_tool
from app.layer4_frameworks.tools.transform_row import make_transform_row_tool

logger = logging.getLogger(__name__)

_REQUIRED_RUNTIME_REF_NAMES = (
    "llm_service_ref",
    "structured_llm_gateway_ref",
)


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def build_tools(dependencies: dict[str, Any]) -> list[Any]:
    """Return explicit CSV-owned tools with dependencies injected by closure."""

    tools = [
        make_convert_json_to_csv_tool(
            file_upload_gateway=dependencies["file_upload_gateway"],
            settings_provider=dependencies["settings_provider"],
        ),
        make_convert_csv_to_json_tool(
            csv_json_gateway=dependencies["csv_json_gateway"],
            settings_provider=dependencies["settings_provider"],
        ),
        make_query_file_data_tool(
            file_preview_gateway=dependencies["file_preview_gateway"],
        ),
        make_get_first_n_rows_tool(
            file_preview_gateway=dependencies["file_preview_gateway"],
        ),
        make_create_file_tool(
            llm_service_ref=dependencies["llm_service_ref"],
            file_upload_gateway=dependencies["file_upload_gateway"],
        ),
        make_transform_column_tool(
            data_factory_gateway_ref=dependencies["data_factory_gateway_ref"],
            structured_llm_gateway_ref=dependencies["structured_llm_gateway_ref"],
            file_preview_gateway=dependencies["file_preview_gateway"],
            settings_provider=dependencies["settings_provider"],
        ),
        make_transform_row_tool(
            data_factory_gateway_ref=dependencies["data_factory_gateway_ref"],
            structured_llm_gateway_ref=dependencies["structured_llm_gateway_ref"],
            file_preview_gateway=dependencies["file_preview_gateway"],
            settings_provider=dependencies["settings_provider"],
        ),
        make_revert_file_tool(
            llm_service_ref=dependencies["llm_service_ref"],
            settings_provider=dependencies["settings_provider"],
        ),
        make_import_file_to_node_tool(
            workflow_node_gateway=dependencies["workflow_node_gateway"],
        ),
        plus,
        minus,
    ]
    logger.info("Loaded %d explicit CSV tools", len(tools))
    return tools


def _configure_use_cases(ioc_container: dict[str, Any]) -> None:
    ioc_container["plan_usecase"] = PlanUseCase()
    ioc_container["review_usecase"] = ReviewUseCase(
        logger_instance=ioc_container["logger"]
    )


def _set_ref(dependencies: dict[str, Any], name: str, value: Any) -> None:
    ref = dependencies.get(name)
    if isinstance(ref, RuntimeDependencyRef) and value is not None:
        ref.set(value)


def _require_runtime_ref(dependencies: dict[str, Any], name: str) -> None:
    ref = dependencies.get(name)
    if not isinstance(ref, RuntimeDependencyRef):
        raise RuntimeError(f"{name} RuntimeDependencyRef is missing from container")
    ref.require()


def _probe_runtime_dependency_refs(dependencies: dict[str, Any]) -> None:
    """Fail fast if SDK-resolved deferred dependencies were not wired."""

    for name in _REQUIRED_RUNTIME_REF_NAMES:
        _require_runtime_ref(dependencies, name)

    if _clean_string(getattr(settings, "DATA_FACTORY_URL", "")):
        _require_runtime_ref(dependencies, "data_factory_gateway_ref")


def configure_sdk_runtime_gateways(container: dict[str, Any]) -> None:
    """Wire SDK-resolved runtime dependencies into per-container references."""

    dependencies = container.get("_dependencies")
    if not isinstance(dependencies, dict):
        raise RuntimeError("CSV agent SDK container is missing _dependencies")

    llm_service = (
        dependencies.get("llm_service")
        or dependencies.get("chat_completion_service")
        or dependencies.get("openai_service")
    )
    structured_llm_gateway = dependencies.get(
        "structured_llm_gateway"
    ) or container.get("structured_llm_gateway")

    logger.info(
        "SDK runtime gateway wiring diagnostics: dependency_keys=%s "
        "container_keys=%s llm_service_class=%s has_acomplete_with_format=%s "
        "existing_structured_gateway_class=%s",
        sorted(str(key) for key in dependencies.keys()),
        sorted(str(key) for key in container.keys()),
        f"{type(llm_service).__module__}.{type(llm_service).__name__}"
        if llm_service is not None
        else None,
        callable(getattr(llm_service, "acomplete_with_format", None))
        if llm_service is not None
        else False,
        f"{type(structured_llm_gateway).__module__}.{type(structured_llm_gateway).__name__}"
        if structured_llm_gateway is not None
        else None,
    )

    if llm_service is not None:
        _set_ref(dependencies, "llm_service_ref", llm_service)
        if structured_llm_gateway is None:
            structured_llm_gateway = SDKStructuredLLMGatewayProvider(llm_service)
            logger.info(
                "Created SDKStructuredLLMGatewayProvider from llm_service_class=%s",
                f"{type(llm_service).__module__}.{type(llm_service).__name__}",
            )

    if structured_llm_gateway is not None:
        dependencies["structured_llm_gateway"] = structured_llm_gateway
        container["structured_llm_gateway"] = structured_llm_gateway
        _set_ref(dependencies, "structured_llm_gateway_ref", structured_llm_gateway)

        plan_usecase = dependencies.get("plan_usecase") or container.get("plan_usecase")
        if isinstance(plan_usecase, PlanUseCase):
            plan_usecase.structured_llm_gateway = structured_llm_gateway
            container.setdefault("plan_usecase", plan_usecase)
            logger.info(
                "Injected structured LLM gateway into PlanUseCase: gateway_class=%s "
                "plan_usecase_class=%s",
                f"{type(structured_llm_gateway).__module__}."
                f"{type(structured_llm_gateway).__name__}",
                f"{type(plan_usecase).__module__}.{type(plan_usecase).__name__}",
            )

    review_usecase = dependencies.get("review_usecase") or container.get(
        "review_usecase"
    )
    if isinstance(review_usecase, ReviewUseCase):
        container.setdefault("review_usecase", review_usecase)

    mcp_client = dependencies.get("mcp_client")
    if mcp_client is not None and _clean_string(
        getattr(settings, "DATA_FACTORY_URL", "")
    ):
        gateway = MCPDataFactoryGatewayProvider(
            mcp_client=mcp_client,
            settings_provider=dependencies["settings_provider"],
            logger=dependencies.get("logger"),
        )
        dependencies["data_factory_gateway"] = gateway
        container["data_factory_gateway"] = gateway
        _set_ref(dependencies, "data_factory_gateway_ref", gateway)
        logger.info("Data Factory gateway configured from SDK MCP client")

    _probe_runtime_dependency_refs(dependencies)


def build_dependencies() -> dict[str, Any]:
    runtime_settings_provider = RuntimeSettingsProvider(settings)
    ioc_container: dict[str, Any] = {
        "settings": settings,
        "logger": StandardLogger(),
        "settings_provider": runtime_settings_provider,
        "csv_json_gateway": CsvJsonGatewayProvider(runtime_settings_provider),
        "workflow_node_gateway": WorkflowNodeGatewayProvider(runtime_settings_provider),
        "file_preview_gateway": FilePreviewGatewayProvider(runtime_settings_provider),
        "file_upload_gateway": FileUploadGatewayProvider(runtime_settings_provider),
        "llm_service_ref": RuntimeDependencyRef[ILLMService]("llm_service"),
        "structured_llm_gateway_ref": RuntimeDependencyRef[IStructuredLLMGateway](
            "structured_llm_gateway"
        ),
        "data_factory_gateway_ref": RuntimeDependencyRef[IDataFactoryGateway](
            "data_factory_gateway"
        ),
    }

    _configure_use_cases(ioc_container)
    ioc_container["worker_tools"] = build_tools(ioc_container)

    logger.info("Dependencies initialized: %s", list(ioc_container.keys()))
    return ioc_container
