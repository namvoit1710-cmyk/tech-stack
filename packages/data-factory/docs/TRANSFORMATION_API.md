# Data Factory Transformation API

This document describes how to use the Transformation API to transform cloud-stored data files using configurable transformation rules.

## Related Docs

- Row-level transformation reference: [ROW_TRANSFORMATION_API.md](./ROW_TRANSFORMATION_API.md)

## Overview

The Transformation API supports:
- Transforming files in CSV, JSON, and XLSX formats
- Version-aware reads and uploads against File Service logical files
- Built-in transformation rules: `filter`, `mapping`, `drop_columns`, `rename_columns`, `case_when_expr`, `insert_row`, `delete_row`
- Row-level transformations with flexible row identification
- OData query support for result inspection
- Custom Polars expressions for complex transformation logic

Supported formats: `csv`, `json`, `xlsx`, `xls`

If a request uses an unsupported format, the API returns a clear error message.

---

## Base URL

```
/api/v1/transformation
```

---

## Transformation Endpoints

### 1. Transform a File

**POST** `/api/v1/transformation`

Transform a cloud file using provided transformation rules.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `file_id` | string | ✅ | - | Logical File Service file ID. Legacy `file_path` is still accepted temporarily. |
| `file_format` | string | ❌ | `"csv"` | Input file format: `csv`, `json`, `xlsx` |
| `output_format` | string | ❌ | `"csv"` | Output file format: `csv`, `json`, `xlsx` |
| `version_id` | string | ❌ | `null` | Optional source version to download. When provided, the same value is used as the upload concurrency token. |
| `rules` | array | ✅ | - | Array of transformation rules |

#### Example Request

Simple example using the current request shape:

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "output_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "rules": [
    {
      "rule_name": "Keep Only Active Users",
      "type": "filter",
      "params": {
        "expression": "pl.col('status') == 'active'"
      }
    },
    {
      "rule_name": "Create Full Name",
      "type": "mapping",
      "params": {
        "expression": "pl.col('first_name') + ' ' + pl.col('last_name')",
        "new_col": "full_name"
      }
    }
  ]
}
```

Legacy-compatible example for teams still sending `file_path`:

```json
{
  "file_path": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "output_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "rules": [
    {
      "rule_name": "Keep Only Active Users",
      "type": "filter",
      "params": {
        "expression": "pl.col('status') == 'active'"
      }
    }
  ]
}
```

What these rules do:

- `filter`: keeps only rows where the expression is true.
- `mapping`: creates or overwrites a column using a Polars expression.

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "output_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "rules": [
    {
      "rule_name": "Filter Active Users",
      "type": "filter",
      "params": {
        "expression": "pl.col('status') == 'active'"
      }
    },
    {
      "rule_name": "Add Full Name",
      "type": "mapping",
      "params": {
        "expression": "pl.col('first_name') + ' ' + pl.col('last_name')",
        "new_col": "full_name"
      }
    }
  ]
}
```

#### Response

```json
{
  "success": true,
  "message": "Transformed successfully (Input rows: 100, Output: 75)",
  "result": {
    "success": true,
    "total_rows": 75,
    "total_columns": 6,
    "download_url": "https://.../api/v1/files/versions/ver-6/download",
    "output_file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
    "output_version_id": "ver-6",
    "source_file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
    "source_version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
    "odata": {
      "result_file_url": "https://.../api/v1/files/versions/ver-6/download",
      "preview": [
        {"id": "1", "first_name": "John", "last_name": "Doe", "status": "active", "full_name": "John Doe"},
        {"id": "2", "first_name": "Jane", "last_name": "Smith", "status": "active", "full_name": "Jane Smith"}
      ]
    }
  }
}
```

If `version_id` is stale, Data Factory returns HTTP `409` with the current latest version ID from File Service.

---

## Transformation Rule Types

### 1. Filter Rule (`filter`)

Filter rows based on a Polars expression that evaluates to boolean.

```json
{
  "rule_name": "Filter High Value Orders",
  "type": "filter",
  "params": {
    "expression": "pl.col('order_value') > 1000"
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `expression` | string | ✅ | Polars boolean expression — rows where this is `True` are kept |

**Common Filter Expressions:**

| Expression | Description |
|------------|-------------|
| `pl.col('status') == 'active'` | Exact string match |
| `pl.col('age') >= 18` | Numeric comparison |
| `pl.col('category').is_in(['A', 'B', 'C'])` | Value in list |
| `pl.col('email').str.contains('@')` | String contains |
| `pl.col('name').is_not_null()` | Not null check |
| `(pl.col('price') > 10) & (pl.col('qty') < 100)` | Combined conditions |

---

### 2. Mapping Rule (`mapping`)

Create or overwrite a column based on a Polars expression. The entire expression is evaluated as Polars code, so you can use any Polars operations including `pl.when().then().otherwise()`.

```json
{
  "rule_name": "Calculate Total",
  "type": "mapping",
  "params": {
    "expression": "pl.col('price') * pl.col('quantity')",
    "new_col": "total_amount"
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `expression` | string | ✅ | Polars expression (evaluated as code) |
| `new_col` | string | ✅ | Name of the column to create or overwrite |

> **Tip:** If `new_col` matches an existing column name, it overwrites that column. This is useful for transforming values in-place.

**Common Mapping Expressions:**

| Expression | Description |
|------------|-------------|
| `pl.col('first') + ' ' + pl.col('last')` | String concatenation |
| `pl.col('price') * 1.1` | Arithmetic operation |
| `pl.col('date').str.to_date('%Y-%m-%d')` | Date parsing |
| `pl.col('text').str.to_uppercase()` | String transformation |
| `pl.col('value').fill_null(0)` | Fill null values |
| `pl.col('value').cast(pl.Float64)` | Type casting |

**Advanced — Conditional mapping with `pl.when`:**

Use `mapping` when you need conditional logic with expression-based results (e.g., referencing column values in `otherwise`):

```json
{
  "rule_name": "Fill Empty Names",
  "type": "mapping",
  "params": {
    "expression": "pl.when(pl.col('name').is_null() | (pl.col('name').cast(pl.Utf8).str.strip_chars() == '')).then(pl.lit('Unknown')).otherwise(pl.col('name'))",
    "new_col": "name"
  }
}
```

```json
{
  "rule_name": "Classify Order Value",
  "type": "mapping",
  "params": {
    "expression": "pl.when(pl.col('order_value') > 5000).then(pl.lit('high')).when(pl.col('order_value') > 1000).then(pl.lit('medium')).otherwise(pl.lit('low'))",
    "new_col": "order_tier"
  }
}
```

---

### 3. Drop Columns Rule (`drop_columns`)

Remove specified columns from the dataset.

```json
{
  "rule_name": "Remove Sensitive Data",
  "type": "drop_columns",
  "params": {
    "columns_to_drop": ["ssn", "credit_card", "password"]
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `columns_to_drop` | array | ✅ | List of column names to remove |

> Columns that don't exist in the dataset are silently skipped.

---

### 4. Rename Columns Rule (`rename_columns`)

Rename columns in the dataset.

```json
{
  "rule_name": "Standardize Column Names",
  "type": "rename_columns",
  "params": {
    "mapping": {
      "FirstName": "first_name",
      "LastName": "last_name",
      "EmailAddress": "email"
    }
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mapping` | object | ✅ | Key-value pairs of old name → new name |

> Only columns that exist in the dataset are renamed. Missing columns are silently skipped.

---

### 5. Case When Expression (`case_when_expr`)

Apply conditional logic to assign **literal values** to a column.

> **Important:** Both `result` and `otherwise` are treated as **literal values**, NOT as Polars expressions. If you write `"otherwise": "pl.col('name')"`, the output will be the literal string `"pl.col('name')"`, not the column value. If you need expression-based results (e.g., referencing a column value), use the [`mapping`](#2-mapping-rule-mapping) type with `pl.when().then().otherwise()` instead.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `column_dest` | string | ✅ | Target column to create or overwrite |
| `conditions` | array | ✅ | Array of when/result pairs |
| `conditions[].when_expression` | string | ✅ | Polars boolean expression (evaluated as code) |
| `conditions[].result` | any | ✅ | **Literal value** to assign when condition is true |
| `otherwise` | any | ❌ | **Literal value** if no conditions match. Defaults to `null` if omitted |

**Example — Categorize into literal values:**

```json
{
  "rule_name": "Categorize Age Groups",
  "type": "case_when_expr",
  "params": {
    "column_dest": "age_group",
    "conditions": [
      {
        "when_expression": "pl.col('age').cast(pl.Int32) < 18",
        "result": "minor"
      },
      {
        "when_expression": "pl.col('age').cast(pl.Int32) < 65",
        "result": "adult"
      }
    ],
    "otherwise": "senior"
  }
}
```

**Example — Flag rows based on condition:**

```json
{
  "rule_name": "Flag High Value Orders",
  "type": "case_when_expr",
  "params": {
    "column_dest": "is_high_value",
    "conditions": [
      {
        "when_expression": "pl.col('order_value').cast(pl.Float64, strict=False) > 5000",
        "result": "YES"
      }
    ],
    "otherwise": "NO"
  }
}
```

**Example — Assign customer tier labels:**

```json
{
  "rule_name": "Customer Tier Label",
  "type": "case_when_expr",
  "params": {
    "column_dest": "tier_label",
    "conditions": [
      {
        "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '1'",
        "result": "Bronze"
      },
      {
        "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '2'",
        "result": "Silver"
      },
      {
        "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '3'",
        "result": "Gold"
      }
    ],
    "otherwise": "Unknown"
  }
}
```

#### `case_when_expr` vs `mapping` — When to Use Which

| Scenario | Use | Example |
|----------|-----|---------|
| Assign **literal strings/numbers** based on conditions | `case_when_expr` | Categorize age → "minor", "adult", "senior" |
| Replace empty values with a **literal default** | `case_when_expr` | If name is empty → "Unknown" |
| Replace empty values but **keep original** otherwise | `mapping` | If name is empty → "Unknown", else → keep original name |
| Result needs to **reference a column** | `mapping` | Combine first + last name conditionally |
| Complex chained expressions | `mapping` | Nested when/then with computed values |

**Correct way to fill empty values while keeping originals (use `mapping`):**

```json
{
  "rule_name": "Fill Empty Names",
  "type": "mapping",
  "params": {
    "expression": "pl.when(pl.col('name').is_null() | (pl.col('name').cast(pl.Utf8).str.strip_chars() == '')).then(pl.lit('Rowan')).otherwise(pl.col('name'))",
    "new_col": "name"
  }
}
```

---

### 6. Insert Row Rule (`insert_row`)

Insert a new row at a specific position.

```json
{
  "rule_name": "Add Header Row",
  "type": "insert_row",
  "params": {
    "identifier_type": "index",
    "index": 0,
    "position": "before",
    "data": {
      "id": "NEW-001",
      "name": "New Record",
      "value": "100"
    }
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `identifier_type` | string | ✅ | `"index"`, `"condition"`, or `"values"` |
| `index` | integer | ❌ | Row index (required when `identifier_type` is `"index"`) |
| `expression` | string | ❌ | Polars expression (required when `identifier_type` is `"condition"`) |
| `match_values` | object | ❌ | Column-value pairs (required when `identifier_type` is `"values"`) |
| `position` | string | ✅ | `"before"`, `"after"`, or `"at_index"` |
| `data` | object | ✅ | Row data as column-value pairs |
| `use_column_indices` | boolean | ❌ | If true, data keys are column indices |

---

### 7. Delete Row Rule (`delete_row`)

Delete rows matching specific criteria.

```json
{
  "rule_name": "Remove Invalid Records",
  "type": "delete_row",
  "params": {
    "identifier_type": "condition",
    "expression": "pl.col('status') == 'invalid'"
  }
}
```

**Row Identifier Types:**

| Type | Required Params | Description |
|------|-----------------|-------------|
| `index` | `index` | Delete row at specific position |
| `condition` | `expression` | Delete rows matching Polars expression |
| `values` | `match_values` | Delete rows with exact column values |

**Examples:**

```json
// Delete by index
{
  "identifier_type": "index",
  "index": 5
}

// Delete by condition
{
  "identifier_type": "condition",
  "expression": "pl.col('age') < 0"
}

// Delete by exact values
{
  "identifier_type": "values",
  "match_values": {
    "id": "123",
    "status": "deleted"
  }
}
```

---

## Row Transformation Endpoint

### Transform Rows

**POST** `/api/v1/transformation/rows`

Perform row-level transformations (insert/delete) on a cloud file with more granular control.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `file_id` | string | ✅ | - | Logical File Service file ID. Legacy `file_path` is still accepted temporarily. |
| `file_format` | string | ❌ | `"csv"` | File format |
| `version_id` | string | ❌ | `null` | Optional source version to download. When provided, the same value is used as the upload concurrency token. |
| `operations` | array | ✅ | - | Array of row operations (max 100) |
| `use_column_indices` | boolean | ❌ | `false` | Use column indices instead of names |

#### Row Operation Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation` | string | ✅ | `"insert"` or `"delete"` |
| `row_identifier` | object | ✅ | Row identification specification |
| `row_identifier.type` | string | ✅ | `"index"`, `"condition"`, or `"values"` |
| `row_identifier.index` | integer | ❌ | Row index (for type `"index"`) |
| `row_identifier.expression` | string | ❌ | Polars expression (for type `"condition"`) |
| `row_identifier.match_values` | object | ❌ | Column-value pairs (for type `"values"`) |
| `data` | object | ❌ | Row data for insert operations |
| `position` | string | ❌ | `"before"`, `"after"`, or `"at_index"` for inserts |

#### Example Request

Simple example using the current request shape:

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('status') == 'inactive'"
      }
    },
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 0
      },
      "position": "after",
      "data": {
        "id": "999999",
        "name": "New Customer",
        "status": "active"
      }
    }
  ]
}
```

Legacy-compatible example for teams still sending `file_path`:

```json
{
  "file_path": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('status') == 'inactive'"
      }
    }
  ]
}
```

What these operations do:

- `delete`: removes all rows matching the row identifier.
- `insert`: inserts one new row before or after a single target row.

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('status') == 'inactive'"
      }
    },
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 0
      },
      "position": "at_index",
      "data": {
        "id": "HEADER",
        "name": "Name Column",
        "status": "Status Column"
      }
    }
  ]
}
```

#### Response

```json
{
  "success": true,
  "affected_rows": 15,
  "total_rows": 86,
  "message": "Row transformations completed successfully. Original rows: 100, Final rows: 86",
  "preview_data": [
    {"id": "HEADER", "name": "Name Column", "status": "Status Column"},
    {"id": "1", "name": "John Doe", "status": "active"}
  ],
  "download_url": "https://storage.example.com/presigned/result.csv"
}
```

---

## Result Query Endpoints

### Query Transformation Results

**GET** `/api/v1/transformation/result/query`

Query transformation result data with OData syntax.

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_id` | string | ✅ | Logical file ID or legacy result file name |
| `version_id` | string | ❌ | Specific version ID. If omitted, the latest current version is used. |
| `$top` | integer | ❌ | Limit number of rows returned |
| `$skip` | integer | ❌ | Skip N rows |
| `$filter` | string | ❌ | Filter expression |
| `$select` | string | ❌ | Select specific columns |
| `$orderby` | string | ❌ | Sort results |

#### Example

```
GET /api/v1/transformation/result/query?file_id=file-123&version_id=ver-6&$top=10&$select=id,name,total
```

---

### Download Transformation Results

**GET** `/api/v1/transformation/result/download`

Generate a download URL for the transformation result file.

When `version_id` is provided, the API returns the specific-version download URL.
When `version_id` is omitted, the API returns the current latest download URL for `file_id`.

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_id` | string | ✅ | Logical file ID |
| `version_id` | string | ❌ | Specific version ID |

#### Response

```json
{
  "file_url": "https://.../api/v1/files/versions/ver-6/download"
}
```

---

## Polars Expression Reference

The transformation API uses Polars expressions. Here are common patterns:

### Column Access

```python
pl.col('column_name')           # Access single column
pl.col('col1', 'col2')          # Access multiple columns
pl.all()                        # All columns
pl.exclude('col_to_skip')       # All except specified
```

### String Operations

```python
pl.col('text').str.to_uppercase()
pl.col('text').str.to_lowercase()
pl.col('text').str.strip_chars()
pl.col('text').str.contains('pattern')
pl.col('text').str.replace('old', 'new')
pl.col('text').str.slice(0, 5)
pl.col('text').str.len_chars()
```

### Numeric Operations

```python
pl.col('value') + 10
pl.col('price') * pl.col('qty')
pl.col('value').abs()
pl.col('value').round(2)
pl.col('value').clip(0, 100)
pl.col('value').cast(pl.Float64)
pl.col('value').cast(pl.Float64, strict=False)  # Returns null instead of error for invalid values
```

### Date Operations

```python
pl.col('date').str.to_date('%Y-%m-%d')
pl.col('timestamp').dt.year()
pl.col('timestamp').dt.month()
pl.col('timestamp').dt.day()
```

### Null Handling

```python
pl.col('value').is_null()
pl.col('value').is_not_null()
pl.col('value').fill_null(0)
pl.col('value').fill_null('default_text')
```

### Conditional Logic

```python
pl.when(pl.col('status') == 'A').then(pl.lit(1)).otherwise(pl.lit(0))
pl.when(condition1).then(result1).when(condition2).then(result2).otherwise(default)
pl.col('category').is_in(['X', 'Y', 'Z'])
```

---

## Expression Quoting Rules

When writing Polars expressions inside JSON strings, be careful with quotes:

| Correct | Wrong | Why |
|---------|-------|-----|
| `"pl.col('name')"` | `"pl.col("name")"` | Inner double quotes break JSON |
| `"pl.col('name')"` | - | Use single quotes inside expression |
| `"pl.col('email').str.contains(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')"` | `...\\...[a-zA-Z]...` | Double-escape backslashes in JSON |

---

## Complete Examples

### Example 1: Full Data Cleaning Pipeline

```json
{
  "file_id": "019d7fba-9951-7648-8e1c-89a7e2da3826",
  "file_format": "csv",
  "output_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "rules": [
    {
      "rule_name": "Remove Duplicates by ID",
      "type": "filter",
      "params": {
        "expression": "pl.col('id').is_first_distinct()"
      }
    },
    {
      "rule_name": "Fill Empty Names",
      "type": "mapping",
      "params": {
        "expression": "pl.when(pl.col('name').is_null() | (pl.col('name').cast(pl.Utf8).str.strip_chars() == '')).then(pl.lit('Unknown')).otherwise(pl.col('name'))",
        "new_col": "name"
      }
    },
    {
      "rule_name": "Normalize Email to Lowercase",
      "type": "mapping",
      "params": {
        "expression": "pl.col('email').str.to_lowercase()",
        "new_col": "email"
      }
    },
    {
      "rule_name": "Categorize Customer Tiers",
      "type": "case_when_expr",
      "params": {
        "column_dest": "tier_label",
        "conditions": [
          {
            "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '1'",
            "result": "Bronze"
          },
          {
            "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '2'",
            "result": "Silver"
          },
          {
            "when_expression": "pl.col('customer_tier').cast(pl.Utf8) == '3'",
            "result": "Gold"
          }
        ],
        "otherwise": "Unknown"
      }
    },
    {
      "rule_name": "Drop Sensitive Columns",
      "type": "drop_columns",
      "params": {
        "columns_to_drop": ["ssn", "internal_notes"]
      }
    },
    {
      "rule_name": "Rename for Export",
      "type": "rename_columns",
      "params": {
        "mapping": {
          "order_value": "total_order_amount",
          "product_code": "sku"
        }
      }
    }
  ]
}
```

### Example 2: Row-Level Operations

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "file_format": "csv",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('status') == 'deleted'"
      }
    },
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 0
      },
      "position": "before",
      "data": {
        "id": "999999",
        "name": "New Customer",
        "email": "new@example.com",
        "status": "active"
      }
    }
  ]
}
```

---

## Error Handling

All endpoints return error responses in the following format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request — Invalid input or rule configuration |
| 500 | Internal Server Error — Transformation failed |

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Column 'X' not found in dataframe` | Referenced column doesn't exist | Check column names in your data |
| `Index X is out of bounds` | Row index exceeds data length | Use valid index within range |
| `Invalid operation type` | Unknown operation specified | Use `insert` or `delete` |
| `expression must be provided` | Missing Polars expression | Add required expression parameter |
| Rule silently skipped | Expression string has syntax error or bad quotes | Check JSON escaping and quote matching |

> **Note:** If a rule fails during execution, it is logged as a warning and skipped — processing continues with the next rule. Check server logs if rules appear to have no effect.

---

## Best Practices

1. **Chain Multiple Rules**: Apply rules in logical order — filter first, then transform, then rename/drop
2. **Use `mapping` for conditional logic that references columns**: `case_when_expr` only supports literal result values
3. **Use single quotes inside expressions**: Double quotes break JSON (`"pl.col('name')"` not `"pl.col("name")"`)
4. **Double-escape backslashes in regex**: `\\d` in JSON becomes `\d` in the expression
5. **Use `strict=False` on casts**: `pl.col('x').cast(pl.Float64, strict=False)` returns null instead of crashing on bad values
6. **Handle nulls explicitly**: Use `fill_null()`, `is_null()`, or `drop_nulls()` to avoid unexpected results
7. **Test with small data first**: Use preview data in responses to verify before processing large files
8. **Limit row operations**: Keep operations under 100 per request for performance
