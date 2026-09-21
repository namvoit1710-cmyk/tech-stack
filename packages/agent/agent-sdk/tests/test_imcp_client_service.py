from agent_sdk.layer2_application.interfaces import IMCPClientService
from agent_sdk.layer2_application.interfaces import __all__ as interfaces_all


class DummyMCPClientService:
    async def get_tools(self) -> list:
        return []

    async def get_filtered_tools(self, tool_names: list[str]) -> list:
        return []


def test_imcp_client_service_importable():
    assert IMCPClientService is not None


def test_imcp_client_service_in_all():
    assert "IMCPClientService" in interfaces_all


def test_imcp_client_service_protocol_has_get_tools():
    assert hasattr(IMCPClientService, "get_tools")


def test_imcp_client_service_protocol_has_get_filtered_tools():
    assert hasattr(IMCPClientService, "get_filtered_tools")


def test_dummy_satisfies_imcp_client_service_protocol():
    svc: IMCPClientService = DummyMCPClientService()
    assert hasattr(svc, "get_tools")
    assert hasattr(svc, "get_filtered_tools")
