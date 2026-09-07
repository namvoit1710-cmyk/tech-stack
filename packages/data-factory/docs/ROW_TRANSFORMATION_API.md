# Row Transformation API

This document is aligned with the current implementation in Data Factory code:
- REST route: app/layer3_adapters/controllers/restful/v1/data_transformation_controller.py
- Use case: app/layer2_application/features/data_transformation/use_cases/row_transformation_usecase.py
- Provider: app/layer4_frameworks/providers/transformation/polars_row_transformer_provider.py
- Storage: app/layer4_frameworks/providers/storage/node_storage_provider.py

## Endpoint

- Method: POST
- Path: /api/v1/transformation/rows
- Full local URL: http://localhost:8000/api/v1/transformation/rows

Router registration:
- main.py includes data_transformation_controller with prefix /api/v1/transformation

## Purpose

Execute row-level operations on a source file:
- insert
- delete
- update

Supported row identifier types:
- index
- condition
- values

## File Input Semantics (Current Behavior)

Request accepts either:
- file_id
- file_path

Resolution logic:
- file_id = dto.file_id or dto.file_path

Current storage behavior also supports:
- logical file IDs from file service
- full http URL values
- local file paths that exist on server filesystem

Recommended for UI and integration:
- use file_id from file-service upload flow

## Request Schema

```json
{
  "file_id": "required-string (recommended)",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert|delete|update",
      "row_identifier": {
        "type": "index|condition|values",
        "index": 0,
        "expression": "pl.col('status') == 'active'",
        "match_values": { "id": "123" }
      },
      "data": { "column": "value" },
      "position": "before|after|at_index",
      "use_column_indices": false,
      "allow_multiple": false
    }
  ],
  "use_column_indices": false,
  "version_id": "optional-string (defaults to latest version when omitted)"
}
```

Notes:
- Recommended request field: file_id (treat as required for clients).
- Legacy compatibility: file_path is still accepted by current API, and mapped as file_id = dto.file_id or dto.file_path.
- Runtime requirement: one of file_id or file_path must be provided, otherwise request is rejected.
- operations is required by schema and must be non-empty by runtime validation.
- max operations per request: 100.
- file_format default is csv.
- version_id is optional; when omitted, backend reads latest file version. If provided, it is passed to storage for version-aware read/write behavior.

## Operation Validation Rules

## Common Rules (All Operations)

- operation is required and must be one of: insert, delete, update.
- row_identifier is required.
- row_identifier.type is required and must be one of: index, condition, values.

Type-specific row_identifier requirements:
- type=index: index is required and must be integer-compatible.
- type=condition: expression is required and must be string.
- type=values: match_values is required and must be object.

## Insert Rules

- data is required and must be object.
- position is required and must be one of: before, after, at_index.
- use_column_indices can be set per operation.

Position behavior:
- before: insert before target row.
- after: insert after target row.
- at_index: uses row_identifier.index directly.
  - index == 0: prepends.
  - index >= len(df): appends.
  - else: inserts at that position.

Target selection behavior for before/after:
- uses _get_target_indices(..., allow_multiple=false).
- condition/values identifier must resolve to exactly one row.
- if multiple match: error.
- if none match: error.

## Delete Rules

- position is not used.
- data is not required.
- delete by index removes one row at index.
- delete by condition removes all matching rows.
- delete by values removes all matching rows.

No allow_multiple gate for delete:
- condition/values can match many rows and all will be deleted.

## Update Rules

- data is required and must be object.
- position is not allowed (if present -> validation error).
- allow_multiple is optional and defaults false.
- allow_multiple must be boolean.

Target selection behavior:
- allow_multiple=false:
  - if multiple rows match condition/values -> error.
  - if zero rows match -> error.
- allow_multiple=true:
  - update all matched rows.

Update semantics:
- partial update by columns present in data.
- other columns remain unchanged.

Current implementation detail:
- update values are written as string values (except null remains null).

## use_column_indices Behavior

Per-operation field:
- operation.use_column_indices controls how operation.data keys are interpreted.

Interpretation:
- false: keys in data are column names.
- true: keys in data are string/integer indices into dataframe columns.

Global request field:
- request.use_column_indices exists in DTO but is not currently applied in provider operation processing.
- practical behavior is driven by operation.use_column_indices.

## Condition Expression Semantics

Condition expressions are evaluated with restricted context:
- available names: pl, col, lit
- builtins are disabled

Examples:
- pl.col('age') > 30
- (pl.col('status') == 'active') & (pl.col('country') == 'VN')

## Values Matching Semantics

Values-based matching builds conjunction across provided keys.

Comparison behavior:
- dataframe column cast to Utf8 (non-strict)
- compared with str(input_value)

Implication:
- numeric value 1 can match input "1".

Null behavior:
- input null uses is_null() comparison.

## Success Response

HTTP 200 body:

```json
{
  "success": true,
  "affected_rows": 1,
  "total_rows": 20,
  "message": "Row transformations completed successfully. Original rows: 19, Final rows: 20",
  "preview_data": [
    { "id": "1", "name": "Alice" }
  ],
  "download_url": "http://..."
}
```

Preview behavior:
- preview rows bounded by ROW_TRANSFORM_PREVIEW_MAX_ROWS (default 10).
- preview columns bounded by ROW_TRANSFORM_PREVIEW_MAX_COLUMNS (default 60).

## Error Behavior (Important for Test Cases)

## Status Code Matrix

- 422 Unprocessable Entity:
  - FastAPI/Pydantic request-shape validation errors.
  - Example: invalid JSON body, missing required schema field operations, wrong base type for operations.

- 400 Bad Request:
  - controller-level ValueError checks before use case call.
  - current checks:
    - missing file_id/file_path
    - empty operations
    - operations length > 100

- 500 Internal Server Error:
  - broad exception handler wraps many downstream errors.
  - includes use-case validation failures and runtime/provider failures.
  - body format:
    - {"detail": "Row transformation failed: ..."}

Observed implementation nuance:
- use-case failures are first converted to HTTP 400 in route logic,
  then caught by broad except Exception and returned as HTTP 500.
- therefore many operation validation failures return 500 instead of 400.

Example invalid operation response:

```json
{
  "detail": "Row transformation failed: 400: Invalid operation type: merge. Must be 'insert', 'delete', or 'update'"
}
```

## Canonical Request Examples

## Insert by Index (before)

```json
{
  "file_id": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": { "type": "index", "index": 2 },
      "data": { "name": "New User", "email": "new@example.com", "age": 25 },
      "position": "before"
    }
  ]
}
```

## Delete by Condition

```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": { "type": "condition", "expression": "pl.col('age') > 65" }
    }
  ]
}
```

## Update by Values (single target)

```json
{
  "file_id": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "update",
      "row_identifier": { "type": "values", "match_values": { "id": 123, "name": "John Doe" } },
      "data": { "email": "john.doe@newmail.com", "age": 31 }
    }
  ]
}
```

## Bulk Update by Condition (allow_multiple=true)

```json
{
  "file_id": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "update",
      "row_identifier": { "type": "condition", "expression": "pl.col('Country') == 'VN'" },
      "allow_multiple": true,
      "data": { "status": "approved" }
    }
  ]
}
```

## QA Test Case Matrix

Use this matrix to derive automated API tests.

## A. Contract and Routing

1. POST /api/v1/transformation/rows returns 200 for valid payload.
2. Route is mounted under /api/v1/transformation prefix.
3. Success payload includes keys: success, affected_rows, total_rows, message, preview_data, download_url.

## B. Request Validation (Shape)

1. Missing operations field -> 422.
2. operations not array -> 422.
3. Malformed JSON -> 422.
4. Missing both file_id and file_path -> 400 with Invalid input wrapper.
5. operations empty array -> 400 with Invalid input wrapper.
6. operations > 100 -> 400 with Invalid input wrapper.

## C. Operation Type Validation

1. Unsupported operation (e.g., merge) -> currently 500 wrapped detail.
2. Missing operation value -> currently 500 wrapped detail.

## D. Row Identifier Validation

1. Missing row_identifier -> currently 500 wrapped detail.
2. Missing row_identifier.type -> currently 500 wrapped detail.
3. Invalid row_identifier.type -> currently 500 wrapped detail.
4. index type without index -> currently 500 wrapped detail.
5. index type with non-int index -> currently 500 wrapped detail.
6. condition type without expression -> currently 500 wrapped detail.
7. values type without match_values -> currently 500 wrapped detail.

## E. Insert Cases

1. Valid insert before index -> 200, affected_rows increments by 1.
2. Valid insert after index -> 200.
3. Valid insert at_index in middle -> 200.
4. Insert at_index >= len -> append behavior, 200.
5. Insert missing data -> currently 500 wrapped detail.
6. Insert missing position -> currently 500 wrapped detail.
7. Insert invalid position -> currently 500 wrapped detail.
8. Insert with use_column_indices=true and valid indices -> 200.
9. Insert with out-of-bounds column index -> currently 500 wrapped detail.

## F. Delete Cases

1. Delete by index valid -> 200, affected_rows = 1.
2. Delete by index out-of-bounds -> currently 500 wrapped detail.
3. Delete by condition matching multiple rows -> 200, all matched rows removed.
4. Delete by condition no matches -> 200, affected_rows may be 0.
5. Delete by values matching multiple rows -> 200, all matched rows removed.
6. Delete by values invalid column key -> currently 500 wrapped detail.

## G. Update Cases

1. Update by index valid -> 200.
2. Update by values single match with allow_multiple omitted -> 200.
3. Update by condition multiple matches with allow_multiple=false -> currently 500 wrapped detail.
4. Update by condition multiple matches with allow_multiple=true -> 200 (bulk update).
5. Update with no matches -> currently 500 wrapped detail.
6. Update missing data -> currently 500 wrapped detail.
7. Update with position present -> currently 500 wrapped detail.
8. Update with non-boolean allow_multiple -> currently 500 wrapped detail.
9. Verify partial update only changes provided columns.
10. Verify updated values are stored as string (except null).

## H. File Format Cases

1. csv format -> 200 when file exists.
2. json format -> 200 when file exists.
3. xlsx format -> 200 when file exists.
4. xls format -> 200 when file exists.
5. dotted format like .csv -> accepted due normalization.
6. unsupported format (e.g., parquet) -> runtime error (currently wrapped as 500 in route).

## I. Versioning and Storage Cases

1. version_id omitted -> latest source behavior.
2. version_id provided and valid -> read/write with version context.
3. stale version conflict from file service -> propagated as runtime failure (route wraps as 500 for rows path).
4. verify download_url is returned and reachable for successful transformation.

## J. Preview Payload Bounds

1. Default preview row count <= 10.
2. Preview column count <= 60 when wide input.
3. When ROW_TRANSFORM_PREVIEW_MAX_ROWS=0, preview_data is empty array.

## K. Multi-operation Sequencing

1. Multiple operations are applied in order.
2. Failure in later operation fails request and no explicit transaction rollback is performed.
3. Validate final output for mixed sequences (delete then insert, insert then update, etc.).

## Notes for Test Authors

- Prefer asserting status code + stable message substring, not full message equality.
- For known wrapped errors, assert detail starts with "Row transformation failed:".
- For condition expressions, include both simple and compound predicate tests.
- For values matching, include numeric-vs-string equivalence tests.

## Changelog

### 2026-05-29

- Updated to match current implementation for insert/delete/update.
- Added file_id/file_path dual-input behavior.
- Added xlsx/xls support and removed parquet from supported list.
- Documented per-operation use_column_indices behavior.
- Documented real status-code behavior including wrapped 500 errors.
- Added comprehensive QA test-case matrix.
