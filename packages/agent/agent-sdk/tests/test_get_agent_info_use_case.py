"""Tests for GetAgentInfoUseCase capabilities reporting."""

from agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case import (
    GetAgentInfoUseCase,
)
from tests.helpers.testing import StubLogger as _StubLogger


class _StubSettings:
    AGENT_TYPE = "tool-agent"
    AGENT_VERSION = "1.0"
    SDK_VERSION = "2.0.0"
    AGENT_DOMAIN = "testing"
    CAPABILITIES = [{"domain": "testing", "action": "run"}]


class _StubSettingsNoCap:
    AGENT_TYPE = "tool-agent"
    AGENT_VERSION = "1.0"
    SDK_VERSION = "2.0.0"
    AGENT_DOMAIN = "testing"
    CAPABILITIES = []


class _StubSettingsNoneCapabilities:
    """Non-Pydantic stub where CAPABILITIES is None — must be normalised to []."""

    AGENT_TYPE = "tool-agent"
    AGENT_VERSION = "1.0"
    SDK_VERSION = "2.0.0"
    AGENT_DOMAIN = "testing"
    CAPABILITIES = None


class _StubSettingsStringCapabilities:
    AGENT_TYPE = "tool-agent"
    AGENT_VERSION = "1.0"
    SDK_VERSION = "2.0.0"
    AGENT_DOMAIN = "testing"
    CAPABILITIES = ["workflow_schema_integration", "reference_canonicalization"]


def test_execute_capabilities_come_from_settings():
    """execute() must return AgentInfo.capabilities populated from settings.CAPABILITIES."""
    uc = GetAgentInfoUseCase(logger=_StubLogger(), settings=_StubSettings())
    info = uc.execute()
    assert info.capabilities == [{"domain": "testing", "action": "run"}]


def test_execute_returns_empty_capabilities_when_settings_capabilities_empty():
    """execute() must return [] when settings.CAPABILITIES is []."""
    uc = GetAgentInfoUseCase(logger=_StubLogger(), settings=_StubSettingsNoCap())
    info = uc.execute()
    assert info.capabilities == []


def test_execute_normalises_none_capabilities_to_empty_list():
    """execute() must return [] when settings.CAPABILITIES is None (regression)."""
    uc = GetAgentInfoUseCase(
        logger=_StubLogger(), settings=_StubSettingsNoneCapabilities()
    )
    info = uc.execute()
    assert info.capabilities == []


def test_execute_returns_correct_agent_info_fields():
    """execute() must still populate all other AgentInfo fields correctly."""
    uc = GetAgentInfoUseCase(logger=_StubLogger(), settings=_StubSettings())
    info = uc.execute()
    assert info.agent_type == "tool-agent"
    assert info.version == "1.0"
    assert info.sdk_version == "2.0.0"
    assert info.domain == "testing"


def test_execute_normalises_string_capabilities_to_dicts():
    """String capabilities must be normalized to dict entries for /info compatibility."""
    uc = GetAgentInfoUseCase(
        logger=_StubLogger(), settings=_StubSettingsStringCapabilities()
    )
    info = uc.execute()
    assert info.capabilities == [
        {"domain": "testing", "action": "workflow_schema_integration"},
        {"domain": "testing", "action": "reference_canonicalization"},
    ]


def test_use_case_does_not_accept_worker_tools():
    """GetAgentInfoUseCase must not store _worker_tools (worker_tools arg removed)."""
    uc = GetAgentInfoUseCase(logger=_StubLogger(), settings=_StubSettings())
    assert not hasattr(uc, "_worker_tools")
