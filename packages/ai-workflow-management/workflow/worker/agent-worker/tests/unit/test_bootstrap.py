import os

from worker_sdk.bootstrap import build_app_container, scan_and_load_features
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    AGENT_INPUT_SCHEMA,
    AGENT_OUTPUT_SCHEMA,
    AiProviderFactory,
)
from app.layer1_domain.value_objects.ai_provider import AiProvider
from app.layer4_frameworks.providers.openai_provider import OpenAiProvider


class TestBootstrap:
    def test_build_app_container_with_worker_params(self):
        worker_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        features_path = os.path.join(worker_dir, "app", "layer2_application", "features")

        provider_factory = AiProviderFactory(
            provider_registry={
                AiProvider.OPENAI.value: OpenAiProvider
            },
        )

        container = build_app_container(
            features_path=features_path,
            base_module="app.layer2_application.features",
            extra_dependencies={
                "input_schema": AGENT_INPUT_SCHEMA,
                "output_schema": AGENT_OUTPUT_SCHEMA,
                "ai_provider_factory": provider_factory,
            },
        )
        assert "execute_task_usecase" in container
        deps = container["_dependencies"]
        assert deps["input_schema"] == AGENT_INPUT_SCHEMA
        assert deps["output_schema"] == AGENT_OUTPUT_SCHEMA
        assert deps["ai_provider_factory"] is provider_factory

    def test_scan_and_load_worker_features(self):
        worker_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        features_path = os.path.join(worker_dir, "app", "layer2_application", "features")
        features = scan_and_load_features(
            features_path=features_path,
            base_module="app.layer2_application.features",
        )
        assert "execute_task_usecase" in features
