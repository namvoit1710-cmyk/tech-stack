def test_build_app_container_exposes_dependencies(app_container):
    container = app_container

    assert "_dependencies" in container
    dependencies = container["_dependencies"]

    assert "agent_graph" in dependencies
    assert "settings" in dependencies
    assert "worker_tools" in dependencies
    assert "workflow_client" in dependencies
    assert dependencies["worker_tools"]


def test_create_app_returns_fastapi_app(app_instance):
    app = app_instance

    assert hasattr(app, "router")
    assert hasattr(app, "openapi")