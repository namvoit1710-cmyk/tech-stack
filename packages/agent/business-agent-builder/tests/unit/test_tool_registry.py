from app.layer2_application.interfaces.register_tool import APP_TOOL_METADATA
from app.layer2_application.tools.suggest_capabilities_tool import suggest_capabilities
from app.layer4_frameworks.tools.tool_registry import scan_all_tools


def test_suggest_capabilities_is_marked_as_app_tool():
    assert getattr(suggest_capabilities, APP_TOOL_METADATA, False) is True


def test_suggest_capabilities_infers_slugs_from_description():
    slugs = suggest_capabilities("draft outreach email for customers")
    assert "draft_email" in slugs
    assert "outreach" in slugs
    assert "customer_support" in slugs


def test_suggest_capabilities_defaults_when_no_hint():
    assert suggest_capabilities("do something unusual") == ["general_assistant"]


def test_scan_all_tools_includes_suggest_capabilities():
    tools = scan_all_tools()
    assert tools, "expected at least one registered tool"
