# Live E2E Tests

These tests call the real File Service using the sample CSV already stored in the repo:

- sample input: `automation/apps/e2e/resources/uploads/test-data.csv`
- service under test: Data Factory REST app
- external dependency: `FILE_SERVER_URL` from `.env`

## What Is Covered

`tests/e2e/test_live_file_service_flows.py` validates:

- validation against a real uploaded file using inline rules
- validation result file upload metadata with `result_file_id` and `result_version_id`
- validation result download and OData query using `file_id` plus optional `version_id`
- transformation against a real uploaded file
- latest-version and specific-version download URL behavior
- stale `version_id` rejection with HTTP `409` and `current_version_id`
- schema inspection on the uploaded sample CSV
- schema mapping generation using mapping hints
- schema preview against sample rows
- schema transform execution that writes an output file to File Service
- schema re-inspection of the produced output file

## Run

From `apps/backend/data-factory`:

```powershell
$env:RUN_LIVE_FILE_SERVICE_TESTS="1"
..\..\..\venv\Scripts\python.exe -m pytest tests/e2e/test_live_file_service_flows.py -q
```

## Notes

- The live tests are opt-in because they upload files to the DEV File Service.
- The transform live test intentionally performs a second write with a stale `version_id` to verify the new conflict behavior.
- The validation live test verifies that result query/download now use File Service `file_id` and optional `version_id`, not legacy result filenames.
- The schema-transform live test uses the repo sample CSV as source data and verifies the produced output schema by reading the generated file back from File Service.