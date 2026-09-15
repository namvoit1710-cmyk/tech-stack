# API Reference: List Workflows & List Runs

Both endpoints live on the **workflow-control-plane-service** (default port 8001). All responses are wrapped in the standard envelope `{ "status": "ok", "data": { ... } }` by the `ResponseWrapperMiddleware`.

---

## List Workflows

```
GET /workflows
```

Returns a paginated list of workflow definitions.

### Query Parameters

| Parameter   | Alias    | Type   | Default | Constraints | Description                                              |
|-------------|----------|--------|---------|-------------|----------------------------------------------------------|
| `$skip`     | `offset` | int    | 0       | >= 0        | Number of items to skip (pagination offset)              |
| `$top`      | `limit`  | int    | 20      | 1 -- 100    | Number of items to return per page                       |
| `$filter`   | —        | string | `""`    | —           | OData filter expression (see [Filter Syntax](#filter-syntax-workflows)) |
| `$orderby`  | —        | string | `""`    | —           | Sort field and direction, e.g. `created_at desc`         |
| `$count`    | —        | bool   | false   | —           | Reserved for future use                                  |

> **Backward compatibility:** `offset` and `limit` are accepted as aliases for `$skip` and `$top`. When both forms are present, the `$`-prefixed parameters take priority.

### Filter Syntax (Workflows)

The `$filter` parameter supports simple OData `eq` expressions joined by `and`:

```
$filter=status eq 'DEPLOYED' and tenant_id eq 'system'
```

**Filterable fields:**

| Field       | Allowed values                               |
|-------------|----------------------------------------------|
| `status`    | `DRAFT`, `VALID`, `DEPLOYED`, `ARCHIVED`     |
| `tenant_id` | Any string tenant identifier                |

### Workflow Statuses

| Status     | Description                                       |
|------------|---------------------------------------------------|
| `DRAFT`    | Workflow is being designed, not yet validated      |
| `VALID`    | Workflow passed validation and is ready to deploy  |
| `DEPLOYED` | Workflow is live and can accept new runs           |
| `ARCHIVED` | Workflow has been retired                          |

### Response

```jsonc
{
  "status": "ok",
  "data": {
    "items": [ /* workflow objects */ ],
    "total": 42,
    "$skip": 0,
    "$top": 20
  }
}
```

**Workflow object fields:**

| Field          | Type         | Description                                |
|----------------|--------------|--------------------------------------------|
| `id`           | string (UUID)| Workflow unique identifier                 |
| `name`         | string       | Display name                               |
| `version`      | string       | Semantic version                           |
| `description`  | string       | Workflow description                       |
| `status`       | string       | One of the [Workflow Statuses](#workflow-statuses) |
| `created_at`   | string (ISO 8601) | Creation timestamp                    |
| `test_run_id`  | string or null | ID of the associated test run, if any    |
| `routing_path` | string or null | URL path for webhook/trigger routing     |
| `main_flow`    | boolean      | Whether this is a main (top-level) flow    |

### Examples

```bash
# Default: first 20 workflows
GET /workflows

# Page 2, 10 items per page
GET /workflows?$skip=10&$top=10

# Only deployed workflows
GET /workflows?$filter=status eq 'DEPLOYED'

# Filter by status and tenant, sorted by creation date
GET /workflows?$filter=status eq 'DRAFT' and tenant_id eq 'acme'&$orderby=created_at desc

# Using backward-compat aliases
GET /workflows?offset=0&limit=50
```

---

## List Runs

```
GET /runs
```

Returns a paginated list of workflow runs. Runs can be listed globally or filtered to a specific workflow.

### Query Parameters

| Parameter   | Alias    | Type   | Default | Constraints | Description                                              |
|-------------|----------|--------|---------|-------------|----------------------------------------------------------|
| `$skip`     | `offset` | int    | 0       | >= 0        | Number of items to skip (pagination offset)              |
| `$top`      | `limit`  | int    | 20      | 1 -- 100    | Number of items to return per page                       |
| `$filter`   | —        | string | `""`    | —           | OData filter expression (see [Filter Syntax](#filter-syntax-runs)) |

> **Backward compatibility:** `offset` and `limit` are accepted as aliases for `$skip` and `$top`. Individual query params (`workflow_id`, `status`, `tenant_id`, `is_test`) are also accepted as legacy aliases. When `$filter` is present, its values take priority over the individual query params.

### Filter Syntax (Runs)

The `$filter` parameter supports simple OData `eq` expressions joined by `and`:

```
$filter=workflow_id eq 'abc-123' and status eq 'RUNNING,FAILED' and tenant_id eq 'acme'
```

**Filterable fields:**

| Field         | Allowed values                                                          |
|---------------|-------------------------------------------------------------------------|
| `workflow_id` | Any workflow UUID                                                       |
| `status`      | Comma-separated: `CREATED`, `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `PAUSED` |
| `tenant_id`   | Any string tenant identifier                                            |
| `is_test`     | `true` or `false`                                                       |

### Run Statuses

| Status      | Description                                          |
|-------------|------------------------------------------------------|
| `CREATED`   | Run has been created but not yet started             |
| `PENDING`   | Run is queued and waiting to execute                 |
| `RUNNING`   | Run is actively executing nodes                      |
| `COMPLETED` | Run finished successfully                            |
| `FAILED`    | Run terminated due to an error                       |
| `CANCELLED` | Run was cancelled by user action                     |
| `PAUSED`    | Run is paused, awaiting external input or resume     |

### Response

```jsonc
{
  "status": "ok",
  "data": {
    "items": [ /* run objects */ ],
    "total": 150,
    "$skip": 0,
    "$top": 20
  }
}
```

**Run object fields:**

| Field           | Type              | Description                                  |
|-----------------|-------------------|----------------------------------------------|
| `id`            | string (UUID)     | Run unique identifier                        |
| `workflow_id`   | string            | ID of the parent workflow                    |
| `workflow_name` | string            | Resolved name of the parent workflow (empty string if workflow was deleted) |
| `status`        | string            | One of the [Run Statuses](#run-statuses)     |
| `started_at`    | string (ISO 8601) | Timestamp when the run started               |
| `completed_at`  | string or null    | Timestamp when the run finished, if complete |
| `error`         | string or null    | Error message if the run failed              |
| `is_test`       | boolean           | Whether this is a test run                   |
| `tenant_id`     | string            | Tenant that owns the run                     |

### Sorting

Results are sorted by `started_at` in **descending** order (newest first). Custom sort is not exposed via query parameters for runs.

### Examples

```bash
# Default: latest 20 runs across all workflows
GET /runs

# Runs for a specific workflow
GET /runs?$filter=workflow_id eq 'abc-123'

# Only running and paused runs
GET /runs?$filter=status eq 'RUNNING,PAUSED'

# Failed runs for a tenant
GET /runs?$filter=status eq 'FAILED' and tenant_id eq 'acme'

# Production runs only, page 3
GET /runs?$filter=is_test eq 'false'&$skip=40&$top=20

# Combine multiple filters with pagination
GET /runs?$filter=workflow_id eq 'abc-123' and status eq 'COMPLETED,FAILED' and tenant_id eq 'acme'&$skip=0&$top=50

# Using backward-compat individual query params
GET /runs?workflow_id=abc-123&status=RUNNING&offset=0&limit=50
```

---

## Pagination Notes

Both endpoints use **offset-based pagination**:

- **Offset/Skip:** number of records to skip before returning results.
- **Limit/Top:** number of records to return per page. Hard-capped at **100**; minimum is **1**; default is **20**.
- **Total:** the `total` field in the response represents the count of all records matching the current filters (not just the current page).

Typical pagination flow:

```
Page 1: ?$skip=0&$top=20   -> items 0-19
Page 2: ?$skip=20&$top=20  -> items 20-39
Page 3: ?$skip=40&$top=20  -> items 40-59
```

## OData Compatibility

Both endpoints use OData-style parameter names (`$skip`, `$top`, `$filter`) and return `$skip`/`$top` in the response.

| OData parameter | Standard alias | Supported on   |
|-----------------|---------------|----------------|
| `$skip`         | `offset`      | Both           |
| `$top`          | `limit`       | Both           |
| `$filter`       | —             | Both           |
| `$orderby`      | —             | Workflows only |
