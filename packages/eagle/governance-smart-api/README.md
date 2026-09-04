# Governance Smart API

`governance-smart-api` is the caller-facing API for request-driven duplicate import.

It composes `smart_service_sdk` into the same FastAPI app and adds governance-specific routes on top. Governance stays thin: it validates the request, resolves the governance use case, and delegates import-job creation to the SDK's in-process background job use case.

For local/demo usage, governance can also accept a dropped CSV file, parse it into JSON rows, save that parsed payload through the shared result repository, and show the saved result inline.

## Architecture

This project follows the same 4-layer structure as the SDK.

```text
governance-smart-api/
├── main.py
├── requirements.txt
└── app/
    ├── bootstrap.py
    ├── layer1_domain/
    │   └── entities/
    │       └── import_data.py
    ├── layer2_application/
    │   ├── interfaces/
    │   └── uis/
    │   └── features/
    │       └── import_data/
    │           └── use_cases/
    │               ├── import_data_usecase.py
    │               └── upload_import_file_usecase.py
    ├── layer3_adapters/
    │   └── controllers/
    │       └── restful/
    │           └── v1/
    │               ├── governance_import_controller.py
    │               ├── governance_ui_console_controller.py
    │               └── health_controller.py
    └── layer4_frameworks/
        ├── config/
        │   └── app_config.py
        └── logger/
            └── app_logger.py
```

### Layer responsibilities

- `layer1_domain`: governance request and response entities.
- `layer2_application`: governance import/upload use cases, request normalization, and inward-facing interfaces.
- `layer3_adapters`: HTTP routes and DTO mapping.
- `layer4_frameworks`: local config, logging, and composition helpers.
- `bootstrap.py`: enriches the shared SDK container with governance-specific dependencies.
- `main.py`: composition root that calls `smart_create_app(...)` and then registers governance routers.

## Runtime composition

The app is built in [`main.py`](./main.py):

1. `smart_create_app(container_enricher=build_governance_dependencies)` creates the SDK FastAPI app.
2. The SDK app builds its shared container during startup.
3. Governance enriches that same container with `import_data_usecase`.
4. Governance also wires a parsed-upload persistence path that reuses the shared SDK result repository.
5. Governance routers are added on top of the SDK app.

Because of that, one process serves both governance routes and SDK routes.

## APIs

### Governance routes

#### `GET /health`

Simple health check.

Response:

```json
{
  "status": "ok"
}
```

#### `POST /governance/import-data`

Creates a duplicate import background job through the SDK.

Request body:

```json
{
  "file_ids": [
    "FILE_001",
    "FILE_002"
  ],
  "tenant_id": "tenant-1"
}
```

Rules:

- `file_ids` is required.
- Empty values are ignored.
- Duplicate `file_id` values are de-duplicated before calling the SDK.
- If no valid file IDs remain, the API returns `400 Bad Request`.

Success response:

Status: `202 Accepted`

```json
{
  "job_id": "job-123",
  "tenant_id": "tenant-1",
  "status": "accepted",
  "file_ids": [
    "FILE_001",
    "FILE_002"
  ],
  "accepted_at": "2026-06-19T00:00:00+00:00",
  "started_at": null,
  "ended_at": null,
  "error_message": "",
  "file_results": []
}
```

Validation error example:

Status: `400 Bad Request`

```json
{
  "detail": "file_ids must contain at least one non-empty file ID"
}
```

#### `POST /governance/import-data/upload`

Uploads a CSV file, parses it into JSON rows, saves the parsed payload, and returns the saved result.

Request:

- `multipart/form-data`
- file field: `file`
- optional form field: `tenant_id`

Success response:

Status: `201 Created`

```json
{
  "result_id": "gov-upload-123",
  "file_name": "records.csv",
  "content_hash": "7f83b1657ff1fc53b92dc18148a1d65dfa135014",
  "tenant_id": "tenant-1",
  "row_count": 2,
  "raw_headers": ["name", "city"],
  "parsed_rows": [
    {"name": "Acme", "city": "Bangkok"},
    {"name": "Bravo", "city": "Singapore"}
  ],
  "created_at": "2026-06-22T12:00:00"
}
```

#### `GET /governance/ui`

Returns a lightweight HTML page for drag-and-drop CSV parsing:

- upload one file
- parse and save the CSV rows
- show the saved parsed-result JSON inline

This page no longer creates governance import jobs and does not poll SDK background-job status.

### SDK routes exposed on the same app

Since governance composes the SDK app, SDK routes are also available from the same server. The main import-related SDK routes are:

- `POST /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{job_id}`

The governance route is the intended caller-facing entrypoint for import submission. The SDK remains the owner of background-job execution and job status. The upload route is now a separate parse-and-save flow and does not trigger SDK import jobs.

## Browser demo

The governance import console is available at:

```text
http://127.0.0.1:8080/governance/ui
```

## Example request

```bash
curl -X POST "http://localhost:8080/governance/import-data" \
  -H "Content-Type: application/json" \
  -d '{
    "file_ids": ["FILE_001", "FILE_002"],
    "tenant_id": "tenant-1"
  }'
```

## Running locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Then open:

```text
http://127.0.0.1:8080/governance/ui
```

Notes:

- `requirements.txt` installs `smart_service_sdk` from `../smart_service_sdk` in editable mode.
- The governance app depends on the SDK container keys created by `smart_create_app(...)`.
- Governance upload parsing now saves parsed CSV JSON through the shared SDK result repository.

## Configuration

Current governance settings are defined in [`app/layer4_frameworks/config/app_config.py`](./app/layer4_frameworks/config/app_config.py):

- `APP_NAME`
- `APP_VERSION`
- `LOG_LEVEL`
- `LOG_FORMAT`

Settings are loaded from `.env` when present.
