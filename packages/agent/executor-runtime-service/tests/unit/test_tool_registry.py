from app.layer4_frameworks.dependency.ioc_container import build_tools


def test_build_tools_registers_explicit_tools():
    tools = build_tools(None)
    tool_names = {getattr(tool, "name", "") for tool in tools}

    assert "echo" in tool_names
    assert "workflow_start_run" in tool_names
    assert "workflow_patch_data" in tool_names
    assert "workflow_submit" in tool_names
    assert "workflow_stop" in tool_names


def test_build_tools_sanitizes_input_schema_for_registry():
    tools = build_tools(None)
    workflow_start_run = next(
        tool for tool in tools if getattr(tool, "name", "") == "workflow_start_run"
    )

    schema = workflow_start_run.get_input_schema()

    assert set(schema) == {"properties", "required"}
    assert schema["required"] == ["workflow_id", "predefined_run_id"]
    assert schema["properties"]["workflow_id"]["type"] == "string"
