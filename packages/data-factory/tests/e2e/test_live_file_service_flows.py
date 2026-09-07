import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient


SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[5]
SAMPLE_CSV_PATH = REPO_ROOT / "automation" / "apps" / "e2e" / "resources" / "uploads" / "test-data.csv"

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


def _load_local_env() -> None:
    env_path = SERVICE_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_local_env()

from bootstrap import build_app_container  # noqa: E402
from app.layer4_frameworks.orm.config.database_config import Base, db_manager  # noqa: E402
from app.layer4_frameworks.config.app_config import settings  # noqa: E402
from app.layer3_adapters.controllers.restful.v1 import (  # noqa: E402
    data_validation_controller,
    data_transformation_controller,
    schema_transform_controller,
)


pytestmark = [
    pytest.mark.e2e,
]

ROW_ID_ALIASES = ["__row_id"]


def _has_min_disk_space(min_free_bytes: int = 64 * 1024 * 1024) -> bool:
    try:
        return shutil.disk_usage(str(SERVICE_ROOT)).free >= min_free_bytes
    except OSError:
        return False


def _file_service_reachable(base_url: str) -> bool:
    try:
        response = requests.get(f"{_normalize_base_url(base_url)}/download/non-existent", timeout=(5, 10))
        return response.status_code in {200, 400, 404}
    except requests.RequestException:
        return False


@pytest.fixture(autouse=True)
def _require_live_prerequisites():
    if os.getenv("RUN_LIVE_FILE_SERVICE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_FILE_SERVICE_TESTS=1 to run live file service tests.")
    if not _has_min_disk_space():
        pytest.skip("Insufficient local disk space for live file download/upload e2e tests.")
    if not _file_service_reachable(settings.FILE_SERVER_URL):
        pytest.skip("Live file service is not reachable; skipping live data-factory e2e tests.")


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if "/docs" in normalized:
        normalized = normalized.split("/docs", 1)[0]
    if normalized.endswith("#"):
        normalized = normalized[:-1]
    if not normalized.endswith("/api/v1"):
        normalized = f"{normalized}/api/v1"
    return normalized.rstrip("/")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.container = await build_app_container()
    yield


def _create_test_client() -> TestClient:
    test_app = FastAPI(title="Clean Data Factory E2E", lifespan=_lifespan)
    test_app.include_router(
        data_validation_controller.router,
        prefix="/api/v1/validation",
        tags=["Validation"],
    )
    test_app.include_router(
        data_transformation_controller.router,
        prefix="/api/v1/transformation",
        tags=["Transformation"],
    )
    test_app.include_router(
        schema_transform_controller.router,
        prefix="/api/v1/schema-transform",
        tags=["Schema Transform"],
    )
    return TestClient(test_app)


def _upload_sample_file(file_path: Path, file_id: str | None = None, previous_version_id: str | None = None) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if file_id:
        params["file_id"] = file_id
    if previous_version_id:
        params["previous_version_id"] = previous_version_id

    with file_path.open("rb") as handle:
        response = requests.post(
            f"{_normalize_base_url(settings.FILE_SERVER_URL)}/upload",
            params=params,
            files={"file": (file_path.name, handle, "text/csv")},
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    return {
        "file_id": payload.get("file_id") or payload.get("id") or "",
        "version_id": payload.get("version_id") or payload.get("current_version_id") or "",
        "filename": payload.get("filename") or file_path.name,
    }


def _renamed_target_schema() -> Dict[str, List[Dict[str, str]]]:
    columns = [
        "customer_id",
        "customer_name",
        "phone_number",
        "email_address",
        "customer_address",
        "age_years",
        "sku",
        "tier_code",
        "amount",
    ]
    return {"columns": [{"name": name} for name in columns]}


def _mapping_hints() -> List[Dict[str, str]]:
    return [
        {"source_field": "id", "target_field": "customer_id"},
        {"source_field": "name", "target_field": "customer_name"},
        {"source_field": "phone", "target_field": "phone_number"},
        {"source_field": "email", "target_field": "email_address"},
        {"source_field": "address", "target_field": "customer_address"},
        {"source_field": "age", "target_field": "age_years"},
        {"source_field": "product_code", "target_field": "sku"},
        {"source_field": "customer_tier", "target_field": "tier_code"},
        {"source_field": "order_value", "target_field": "amount"},
    ]


def _schema_transform_rules(mapping_result: Dict[str, object]) -> List[Dict[str, object]]:
    rules = list(mapping_result.get("suggested_rules") or [])
    rules.append({"type": "drop_columns", "params": {"columns": ROW_ID_ALIASES}})
    return rules


def test_live_transform_round_trip_with_version_conflict():
    uploaded = _upload_sample_file(SAMPLE_CSV_PATH)
    assert uploaded["file_id"]
    assert uploaded["version_id"]

    with _create_test_client() as client:
        transform_response = client.post(
            "/api/v1/transformation",
            json={
                "file_id": uploaded["file_id"],
                "file_format": "csv",
                "output_format": "csv",
                "version_id": uploaded["version_id"],
                "rules": [
                    {
                        "rule_name": "Remove address column",
                        "type": "drop_columns",
                        "params": {"columns_to_drop": ["address"]},
                    }
                ],
            },
        )

        assert transform_response.status_code == 200, transform_response.text
        body = transform_response.json()
        result = body["result"]
        assert result["source_file_id"] == uploaded["file_id"]
        assert result["source_version_id"] == uploaded["version_id"]
        assert result["output_file_id"] == uploaded["file_id"]
        assert result["output_version_id"]
        assert result["output_version_id"] != uploaded["version_id"]

        latest_download = client.get(
            "/api/v1/transformation/result/download",
            params={"file_id": uploaded["file_id"]},
        )
        assert latest_download.status_code == 200, latest_download.text
        assert latest_download.json()["file_url"].endswith(f"/download/{uploaded['file_id']}")

        versioned_download = client.get(
            "/api/v1/transformation/result/download",
            params={"file_id": uploaded["file_id"], "version_id": result["output_version_id"]},
        )
        assert versioned_download.status_code == 200, versioned_download.text
        assert result["output_version_id"] in versioned_download.json()["file_url"]

        query_response = client.get(
            "/api/v1/transformation/result/query",
            params={
                "file_id": uploaded["file_id"],
                "version_id": result["output_version_id"],
                "$top": "2",
                "$select": "id,name,order_value",
            },
        )
        assert query_response.status_code == 200, query_response.text
        query_body = query_response.json()
        rows = query_body.get("value") or query_body.get("data") or []
        assert len(rows) >= 1

        stale_response = client.post(
            "/api/v1/transformation",
            json={
                "file_id": uploaded["file_id"],
                "file_format": "csv",
                "output_format": "csv",
                "version_id": uploaded["version_id"],
                "rules": [
                    {
                        "rule_name": "Try stale version again",
                        "type": "drop_columns",
                        "params": {"columns_to_drop": ["phone"]},
                    }
                ],
            },
        )

        assert stale_response.status_code == 409, stale_response.text
        detail = stale_response.json()["detail"]
        assert detail["file_id"] == uploaded["file_id"]
        assert detail["requested_version_id"] == uploaded["version_id"]
        assert detail["current_version_id"] == result["output_version_id"]


def test_live_validation_with_result_query_and_download():
    uploaded = _upload_sample_file(SAMPLE_CSV_PATH)
    assert uploaded["file_id"]
    assert uploaded["version_id"]

    with _create_test_client() as client:
        validate_response = client.post(
            "/api/v1/validation",
            json={
                "file_path": uploaded["file_id"],
                "file_format": "csv",
                "rules": [
                    {
                        "rule_name": "Email is required",
                        "type": "required",
                        "params": {"columns": ["email"]},
                        "error_message": "Email is required",
                    },
                    {
                        "rule_name": "ID is unique",
                        "type": "unique",
                        "params": {"columns": ["id"]},
                        "error_message": "ID must be unique",
                    },
                ],
            },
        )

        assert validate_response.status_code == 200, validate_response.text
        validate_result = validate_response.json()["result"]
        odata_result = validate_result["odata"]
        assert isinstance(odata_result["data"], list)
        assert odata_result["result_file_id"]

        download_response = client.get(
            "/api/v1/validation/result/download",
            params={
                "file_id": odata_result["result_file_id"],
                "version_id": odata_result.get("result_version_id"),
            },
        )
        assert download_response.status_code == 200, download_response.text
        download_body = download_response.json()
        assert download_body["file_url"]

        query_response = client.get(
            "/api/v1/validation/result/query",
            params={
                "file_id": odata_result["result_file_id"],
                "version_id": odata_result.get("result_version_id"),
                "$top": "2",
                "$select": "id,email,has_validation_error",
            },
        )
        assert query_response.status_code == 200, query_response.text
        query_body = query_response.json()
        rows = query_body.get("value") or query_body.get("data") or []
        assert len(rows) >= 1


def test_live_schema_transform_with_sample_csv():
    uploaded = _upload_sample_file(SAMPLE_CSV_PATH)
    assert uploaded["file_id"]
    assert uploaded["version_id"]

    with _create_test_client() as client:
        inspect_response = client.post(
            "/api/v1/schema-transform/inspect",
            json={
                "file_id": uploaded["file_id"],
                "file_format": "csv",
                "version_id": uploaded["version_id"],
                "sample_size": 3,
            },
        )

        assert inspect_response.status_code == 200, inspect_response.text
        source_schema = inspect_response.json()["result"]
        assert source_schema["columns"]

        target_schema = _renamed_target_schema()
        mapping_response = client.post(
            "/api/v1/schema-transform/generate-mapping",
            json={
                "source_schema": source_schema,
                "target_schema": target_schema,
                "source_type": "file",
                "mapping_hints": _mapping_hints(),
            },
        )

        assert mapping_response.status_code == 200, mapping_response.text
        mapping_result = mapping_response.json()["result"]
        assert len(mapping_result["mappings"]) == 9
        transform_rules = _schema_transform_rules(mapping_result)

        preview_response = client.post(
            "/api/v1/schema-transform/preview",
            json={
                "sample_data": source_schema["sample_data"],
                "rules": transform_rules,
                "file_format": "json",
                "target_schema": target_schema,
            },
        )

        assert preview_response.status_code == 200, preview_response.text
        preview_result = preview_response.json()["result"]
        assert preview_result["matches_target_schema"] is True

        execute_response = client.post(
            "/api/v1/schema-transform",
            json={
                "source_file_id": uploaded["file_id"],
                "source_version_id": uploaded["version_id"],
                "file_format": "csv",
                "output_format": "csv",
                "target_schema": target_schema,
                "field_mapping": mapping_result["mappings"],
                "rules": [{"type": "drop_columns", "params": {"columns": ROW_ID_ALIASES}}],
            },
        )

        assert execute_response.status_code == 200, execute_response.text
        execute_result = execute_response.json()["result"]
        assert execute_result["output_file_id"]
        assert execute_result["output_version_id"]
        assert execute_result["source_file_id"] == uploaded["file_id"]

        output_inspect_response = client.post(
            "/api/v1/schema-transform/inspect",
            json={
                "file_id": execute_result["output_file_id"],
                "file_format": "csv",
                "version_id": execute_result["output_version_id"],
                "sample_size": 2,
            },
        )

        assert output_inspect_response.status_code == 200, output_inspect_response.text
        output_columns = [
            item["name"]
            for item in output_inspect_response.json()["result"]["columns"]
            if item["name"] not in ROW_ID_ALIASES
        ]
        assert output_columns == [item["name"] for item in target_schema["columns"]]
