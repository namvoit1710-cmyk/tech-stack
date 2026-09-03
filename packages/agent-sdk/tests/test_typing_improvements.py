import inspect


class TestFlowGraphBuilderInitAnnotations:
    def test_init_checkpointer_has_none_union_annotation(self):
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        sig = inspect.signature(FlowGraphBuilder.__init__)
        params = sig.parameters
        assert (
            "checkpointer" in params
        ), "FlowGraphBuilder.__init__ must have checkpointer param"
        assert (
            params["checkpointer"].annotation != inspect.Parameter.empty
        ), "checkpointer must have a type annotation"

    def test_init_deps_has_annotation(self):
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        sig = inspect.signature(FlowGraphBuilder.__init__)
        params = sig.parameters
        assert (
            params["deps"].annotation != inspect.Parameter.empty
        ), "deps must have a type annotation"

    def test_init_log_has_annotation(self):
        from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
            FlowGraphBuilder,
        )

        sig = inspect.signature(FlowGraphBuilder.__init__)
        params = sig.parameters
        assert (
            params["log"].annotation != inspect.Parameter.empty
        ), "log must have a type annotation"


class TestHanaCheckpointSaverInitAnnotations:
    def test_init_log_has_annotation(self):
        from agent_sdk.layer4_frameworks.persistence.checkpoint_store import (
            HanaCheckpointSaver,
        )

        sig = inspect.signature(HanaCheckpointSaver.__init__)
        params = sig.parameters
        assert "log" in params, "HanaCheckpointSaver.__init__ must have log param"
        assert (
            params["log"].annotation != inspect.Parameter.empty
        ), "log must have a type annotation"

    def test_init_db_has_annotation(self):
        from agent_sdk.layer4_frameworks.persistence.checkpoint_store import (
            HanaCheckpointSaver,
        )

        sig = inspect.signature(HanaCheckpointSaver.__init__)
        params = sig.parameters
        assert (
            params["db"].annotation != inspect.Parameter.empty
        ), "db must have a type annotation"


class TestCreateCheckpointerAnnotations:
    def test_hana_connection_manager_has_annotation(self):
        from agent_sdk.layer4_frameworks.persistence.checkpoint_store import (
            create_checkpointer,
        )

        sig = inspect.signature(create_checkpointer)
        params = sig.parameters
        assert (
            "hana_connection_manager" in params
        ), "create_checkpointer must have hana_connection_manager param"
        assert (
            params["hana_connection_manager"].annotation != inspect.Parameter.empty
        ), "hana_connection_manager must have a type annotation"

    def test_settings_has_annotation(self):
        from agent_sdk.layer4_frameworks.persistence.checkpoint_store import (
            create_checkpointer,
        )

        sig = inspect.signature(create_checkpointer)
        params = sig.parameters
        assert (
            params["settings"].annotation != inspect.Parameter.empty
        ), "settings must have a type annotation"


class TestRunAgentAnnotations:
    def test_run_agent_features_path_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["features_path"].annotation != inspect.Parameter.empty
        ), "features_path must have a type annotation"

    def test_run_agent_base_module_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["base_module"].annotation != inspect.Parameter.empty
        ), "base_module must have a type annotation"

    def test_run_agent_extra_dependencies_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["extra_dependencies"].annotation != inspect.Parameter.empty
        ), "extra_dependencies must have a type annotation"

    def test_run_agent_agent_graph_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["agent_graph"].annotation != inspect.Parameter.empty
        ), "agent_graph must have a type annotation"

    def test_run_agent_agent_graph_factory_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["agent_graph_factory"].annotation != inspect.Parameter.empty
        ), "agent_graph_factory must have a type annotation"

    def test_run_agent_local_tools_has_annotation(self):
        from agent_sdk.runner import run_agent

        sig = inspect.signature(run_agent)
        params = sig.parameters
        assert (
            params["local_tools"].annotation != inspect.Parameter.empty
        ), "local_tools must have a type annotation"
