import os

from worker_sdk.bootstrap import build_app_container, scan_and_load_features
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MAPPING_INPUT_SCHEMA,
    MAPPING_OUTPUT_SCHEMA,
)
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine


class TestBootstrap:
    def test_build_app_container_with_worker_params(self):
        worker_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        features_path = os.path.join(worker_dir, "app", "layer2_application", "features")

        engine = DefaultMappingEngine()

        container = build_app_container(
            features_path=features_path,
            base_module="app.layer2_application.features",
            extra_dependencies={
                "input_schema": MAPPING_INPUT_SCHEMA,
                "output_schema": MAPPING_OUTPUT_SCHEMA,
                "mapping_engine": engine,
            },
        )
        assert "execute_task_usecase" in container
        deps = container["_dependencies"]
        assert deps["input_schema"] == MAPPING_INPUT_SCHEMA
        assert deps["output_schema"] == MAPPING_OUTPUT_SCHEMA
        assert deps["mapping_engine"] is engine

    def test_scan_and_load_worker_features(self):
        worker_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        features_path = os.path.join(worker_dir, "app", "layer2_application", "features")
        features = scan_and_load_features(
            features_path=features_path,
            base_module="app.layer2_application.features",
        )
        assert "execute_task_usecase" in features
