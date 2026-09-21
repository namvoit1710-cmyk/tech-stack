"""Unit tests for TenantContext value object."""

from agent_sdk.layer1_domain.entities.tenant_context import TenantContext


def test_tenant_context_required_fields():
    """TenantContext must accept tenant_id, user_id, conv_id."""
    ctx = TenantContext(
        tenant_id="acme",
        user_id="u1",
        conv_id="conv-abc",
    )
    assert ctx.tenant_id == "acme"
    assert ctx.user_id == "u1"
    assert ctx.conv_id == "conv-abc"


def test_tenant_context_source_default():
    """source defaults to 'api'."""
    ctx = TenantContext(tenant_id="t1", user_id="u1", conv_id="c1")
    assert ctx.source == "api"


def test_tenant_context_correlation_id_optional():
    """correlation_id is optional and defaults to None."""
    ctx = TenantContext(tenant_id="t1", user_id="u1", conv_id="c1")
    assert ctx.correlation_id is None


def test_tenant_context_correlation_id_can_be_set():
    ctx = TenantContext(
        tenant_id="t1",
        user_id="u1",
        conv_id="c1",
        correlation_id="corr-xyz",
    )
    assert ctx.correlation_id == "corr-xyz"


def test_tenant_context_metadata_optional():
    """metadata is optional and defaults to empty dict."""
    ctx = TenantContext(tenant_id="t1", user_id="u1", conv_id="c1")
    assert ctx.metadata == {}


def test_tenant_context_metadata_can_be_set():
    ctx = TenantContext(
        tenant_id="t1",
        user_id="u1",
        conv_id="c1",
        metadata={"request_id": "r1"},
    )
    assert ctx.metadata["request_id"] == "r1"


def test_tenant_context_source_can_be_overridden():
    ctx = TenantContext(
        tenant_id="t1",
        user_id="u1",
        conv_id="c1",
        source="kafka",
    )
    assert ctx.source == "kafka"


def test_tenant_context_from_agent_request():
    """TenantContext.from_request factory populates from AgentRequest fields."""
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    req = AgentRequest(
        message="hello",
        conv_id="conv-1",
        user_id="user-1",
        tenant_id="tenant-1",
        source="kafka",
        correlation_id="corr-1",
    )
    ctx = TenantContext.from_request(req)
    assert ctx.tenant_id == "tenant-1"
    assert ctx.user_id == "user-1"
    assert ctx.conv_id == "conv-1"
    assert ctx.source == "kafka"
    assert ctx.correlation_id == "corr-1"
