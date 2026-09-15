# Node Artifacts — Design Document

## Problem

Today every node output is a single CSV file reference. There is no way to:
- Attach additional files to a node (reports, charts, logs)
- Distinguish "primary data" from "supplementary artifacts"
- Have a worker produce multiple output files
- Have the frontend upload/download non-CSV files tied to a node

## Design: Extend `__file_ref` with Artifacts Array

The existing `__file_ref` structure gains an optional `artifacts` list. The primary CSV data remains the top-level file reference (backward compatible). Artifacts are additional files attached to the same node output.

### Data Model

```python
# Current (unchanged — primary data)
{
    "__file_ref": True,
    "file_id": "csv-main-001",
    "content_type": "text/csv",
    "row_count": 50,
    "columns": ["name", "email", "status"],
    "extracted": {
        "name": {"value": "Alice"},
        ...
    },

    # NEW: artifacts array
    "artifacts": [
        {
            "file_id": "report-pdf-002",
            "name": "monthly_report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 245000,
            "artifact_type": "report",        # semantic tag: report, chart, log, export, attachment
            "created_at": "2026-03-29T10:00:00Z"
        },
        {
            "file_id": "chart-png-003",
            "name": "revenue_chart.png",
            "content_type": "image/png",
            "size_bytes": 85000,
            "artifact_type": "chart",
            "created_at": "2026-03-29T10:00:01Z"
        }
    ]
}
```

### Backward Compatibility

- If `artifacts` is absent or empty, the system behaves exactly as today
- All existing code that reads `__file_ref`, `file_id`, `extracted`, `columns` is unaffected
- `is_file_ref()` check unchanged — still looks for `__file_ref: True`
- Variable resolution (`{{$node.field}}`) still uses the primary CSV data
- Context summary, data-info links, OData queries all work on the primary `file_id`

### Artifact Types (Extensible)

| artifact_type | Description | Phase |
|---------------|-------------|-------|
| `data` | Additional CSV/tabular data | Phase 1 (now) |
| `export` | Exported version (Excel, JSON) | Phase 1 (now) |
| `report` | Generated report (PDF, HTML) | Future |
| `chart` | Visualization (PNG, SVG) | Future |
| `log` | Execution log / debug output | Future |
| `attachment` | User-uploaded file | Future |

Phase 1 supports `data` and `export` types (CSV content).

---

## API Design

### List Artifacts for a Node

```
GET /runs/{run_id}/nodes/{node_id}/artifacts
```

Response:
```json
{
    "run_id": "...",
    "node_id": "...",
    "primary_file_id": "csv-main-001",
    "artifacts": [
        {
            "file_id": "report-002",
            "name": "monthly_report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 245000,
            "artifact_type": "report",
            "created_at": "2026-03-29T10:00:00Z",
            "links": {
                "download": "/files/report-002"
            }
        }
    ]
}
```

### Add Artifact to a Node (Worker or Frontend)

```
POST /runs/{run_id}/nodes/{node_id}/artifacts
Content-Type: multipart/form-data

file: <binary>
name: "exported_data.csv"
artifact_type: "export"
```

Response:
```json
{
    "file_id": "new-artifact-004",
    "name": "exported_data.csv",
    "content_type": "text/csv",
    "artifact_type": "export"
}
```

### Download Artifact

Uses existing file endpoint:
```
GET /files/{file_id}
```

### Delete Artifact

```
DELETE /runs/{run_id}/nodes/{node_id}/artifacts/{file_id}
```

---

## How Workers Produce Artifacts

### Option A: Via output_metadata (simple)

Worker returns artifacts in `output_metadata`:

```python
# Worker returns:
{
    "status_code": 200,
    "response_body": {...},
    "_artifacts": [
        {
            "file_id": "worker-uploaded-file-id",
            "name": "response_export.csv",
            "content_type": "text/csv",
            "artifact_type": "export"
        }
    ]
}
```

`CompleteTaskUseCase` detects `_artifacts` key, extracts it before CSV conversion, and appends to the file reference's `artifacts` array.

### Option B: Via Worker Function (advanced)

Worker exposes an `upload_artifact` function:

```python
WorkerFunction(
    name="upload_artifact",
    handler=upload_artifact_handler,
)
```

Called during or after execution to attach files.

---

## How Frontend Handles Artifacts

### Display

1. Frontend calls `GET /runs/{run_id}/nodes/{node_id}/artifacts`
2. Renders artifact list alongside the primary data grid
3. Each artifact shows: name, type icon, size, download link

### Upload (INPUT node)

1. Frontend uploads file via `POST /files` → gets `file_id`
2. Attaches as artifact via `POST /runs/{run_id}/nodes/{node_id}/artifacts`
3. Or includes `file_id` in the `updated_input` dict when resuming

### Download

Direct link: `GET /files/{file_id}` with appropriate Content-Type header.

---

## Socket.IO Events

New event when artifacts change:

```python
@dataclass
class ArtifactAdded(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    file_id: str = ""
    name: str = ""
    content_type: str = ""
    artifact_type: str = ""
```

Topic: `artifact.added` — frontend can refresh the artifact list.

---

## Implementation Plan — Phase 1 (CSV Only)

### Step 1: Extend data_reference.py

Add `artifacts` field handling:
- `build_file_ref()` accepts optional `artifacts` list
- `add_artifact(file_ref, artifact)` helper to append
- `get_artifacts(file_ref)` helper to read

**File:** `app/layer1_domain/entities/data_reference.py`

### Step 2: Worker output → artifacts extraction

In `CompleteTaskUseCase`, after building `task_output`:
- Check for `_artifacts` key in `output_metadata`
- Extract artifacts list
- Upload any inline artifact data to file service
- Append to the file reference's `artifacts` array

**File:** `app/layer2_application/features/complete_task/use_cases/complete_task_use_case.py`

### Step 3: REST endpoints

New use case: `get_node_artifacts` + `add_node_artifact`
- `GET /runs/{run_id}/nodes/{node_id}/artifacts` — read from context.variables[node_id].artifacts
- `POST /runs/{run_id}/nodes/{node_id}/artifacts` — upload file + append to artifacts array

**Files:**
- New: `app/layer2_application/features/get_node_artifacts/`
- New: `app/layer2_application/features/add_node_artifact/`
- New: `app/layer3_adapters/controllers/restful/v1/artifact_controller.py`

### Step 4: Socket.IO event

Add `ArtifactAdded` event to `events.py`, register in broadcasting publisher and socketio config.

**Files:**
- `app/layer1_domain/events.py`
- `app/layer4_frameworks/realtime/broadcasting_publisher.py`
- `app/layer2_application/features/get_socketio_config/use_cases/get_socketio_config_use_case.py`

### Step 5: Context & data-info enrichment

- `GetRunContextUseCase`: Include artifact count in summary
- `GetNodeDataInfoUseCase`: Include artifacts link
- `GetNodeExecutionUseCase`: Include artifacts in execution entries

**Files:**
- `app/layer2_application/features/get_run_context/use_cases/get_run_context_use_case.py`
- `app/layer2_application/features/get_node_data_info/use_cases/get_node_data_info_use_case.py`

### Step 6: Tests

- Unit test: build_file_ref with artifacts, add/get artifacts
- Unit test: CompleteTaskUseCase extracts _artifacts from output_metadata
- Unit test: artifact REST endpoints
- Integration test: worker produces output with _artifacts → frontend reads them

### Verification

```bash
cd workflow-control-plane-service
python3 -m pytest tests/ -v
```

---

## Future Phases

| Phase | What | When |
|-------|------|------|
| Phase 1 | CSV artifacts (data, export) | Now |
| Phase 2 | Binary file support (PDF, image) in file service | When file service supports binary storage |
| Phase 3 | Worker SDK artifact upload helper | After Phase 2 |
| Phase 4 | Frontend artifact viewer (PDF preview, image display) | After Phase 2 |
| Phase 5 | Artifact versioning (track edits over time) | After Phase 3 |
