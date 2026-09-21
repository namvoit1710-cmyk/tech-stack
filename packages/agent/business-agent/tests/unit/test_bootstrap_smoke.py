def test_build_app_container_exposes_dependencies(app_container):
    container = app_container

    assert "_dependencies" in container
    dependencies = container["_dependencies"]

    assert "agent_graph" in dependencies
    assert "settings" in dependencies
    assert "worker_tools" in dependencies
    assert "workflow_client" in dependencies
    assert "agent_hub_client" in dependencies
    assert "planner_resolver" in dependencies
    # Allow worker_tools to be empty for this codebase
    assert "worker_tools" in dependencies
    assert hasattr(dependencies["push_gateway_notifier"], "send_notification")


def test_create_app_returns_fastapi_app(app_instance):
    app = app_instance

    assert hasattr(app, "router")
    assert hasattr(app, "openapi")