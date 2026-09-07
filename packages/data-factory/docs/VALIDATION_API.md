# Data Factory Validation API

This document describes how to use the Validation API to validate cloud-stored data files against configurable rule sets.

## Overview

The Validation API supports:
- Validating files in CSV, JSON, and XLSX formats
- Built-in validation rules: `required`, `unique`, `set_unique`, `expression`
- Custom Polars expressions for complex validation logic
- OData query support for result inspection
- Rule set management for reusable validation configurations

---

## Base URL

```
/api/v1/validation
```

---

## Validation Endpoints

### 1. Validate a File

**POST** `/api/v1/validation`

Validate a cloud file against provided rules or a saved rule set.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `file_id` | string | ✅ | - | Logical File Service file ID. Legacy `file_path` is still accepted temporarily. |
| `version_id` | string | ❌ | `null` | Optional source version ID. When provided, validation reads that exact file version from File Service. |
| `file_format` | string | ❌ | `"csv"` | File format: `csv`, `json`, `xlsx` |
| `result_mode` | string | ❌ | `"errors_only"` | Validation result file mode: `errors_only` or `full_file` |
| `data_schema` | object | ❌ | `null` | Schema definition mapping column names to types |
| `rules` | array | ❌ | `null` | Inline validation rules |
| `rule_set_id` | string | ❌ | `null` | ID of saved rule set |

> **Note:** Either `rules` or `rule_set_id` must be provided.
>
> **Versioning behavior:** `version_id` applies to the input source file being validated. The validation output is still uploaded as a separate result artifact with its own `result_file_id` and `result_version_id`.
>
> **Result file behavior:** Use `result_mode = "errors_only"` for a smaller detailed-error file, or `result_mode = "full_file"` to upload the full annotated dataset. `full_file` can produce much larger artifacts and is more likely to hit file-service size or timeout limits on large datasets.

#### Example Request

```json
{
  "file_id": "7feaacd1-9b73-48ae-beb7-b2e8d5122e17",
  "version_id": "0195f0cc-b24d-7d7c-a9e2-fd5c4dc7cdbe",
  "file_format": "csv",
  "result_mode": "errors_only",
  "data_schema": {
    "id": "string",
    "email": "string",
    "age": "integer",
    "order_value": "float",
    "customer_tier": "integer"
  },
  "rules": [
    {
      "rule_name": "Check Unique ID",
      "type": "unique",
      "error_message": "Duplicate IDs found",
      "params": {
        "columns": ["id"]
      }
    },
    {
      "rule_name": "Check Unique Email",
      "type": "unique",
      "error_message": "Duplicate Emails found",
      "params": {
        "columns": ["email"]
      }
    },
    {
      "rule_name": "Validate Email Format",
      "type": "expression",
      "error_message": "Email format is invalid",
      "params": {
        "columns": ["email"],
        "expression": "pl.col('email').str.contains(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')"
      }
    },
    {
      "rule_name": "Check Positive Order Value",
      "type": "expression",
      "error_message": "Order value cannot be negative",
      "params": {
        "columns": ["order_value"],
        "expression": "pl.col('order_value') >= 0"
      }
    },
    {
      "rule_name": "Check Valid Tier",
      "type": "expression",
      "error_message": "Customer Tier must be 1, 2, or 3",
      "params": {
        "columns": ["customer_tier"],
        "expression": "pl.col('customer_tier').is_in([1, 2, 3])"
      }
    }
  ]
}
```

#### Response

```json
{
  "success": true,
  "message": "Validation completed. Found 5/100 rows with errors.",
  "result": {
    "total_rows": 100,
    "valid_rows": 95,
    "invalid_rows": 5,
    "odata": {
      "type": "getDetailError",
      "fields": ["rule_name", "violate", "error_message"],
      "data": [
        {
          "rule_name": "Required ID",
          "violate": 3,
          "error_message": "ID is required",
          "error_code": {"err_0": ["id"]}
        },
        {
          "rule_name": "Unique Email",
          "violate": 2,
          "error_message": "Email must be unique",
          "error_code": {"err_1": ["email"]}
        }
      ],
      "result_file_url": "https://storage.example.com/results/validation_result.csv"
    }
  }
}
```

---

### 2. Query Validation Results

**GET** `/api/v1/validation/result/query`

Query validation result data with OData syntax.

If `result_mode = "errors_only"`, the validation result file contains detailed error rows only. If `result_mode = "full_file"`, the result file contains the full dataset plus validation columns and `has_validation_error`.

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_name` | string | ✅ | Name of the result file |
| `$top` | integer | ❌ | Limit number of rows returned |
| `$skip` | integer | ❌ | Skip N rows |
| `$filter` | string | ❌ | Filter expression |
| `$select` | string | ❌ | Select specific columns |
| `$orderby` | string | ❌ | Sort results |

#### Example

```
GET /api/v1/validation/result/query?file_name=result.csv&$top=10&$filter=has_validation_error eq true
```

---

### 3. Download Validation Results

**GET** `/api/v1/validation/result/download`

Generate a download URL for the validation result file.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_name` | string | ✅ | - | Name of result file |
| `redirect` | boolean | ❌ | `false` | If `true`, redirects to download URL |

#### Response

```json
{
  "file_url": "https://storage.example.com/presigned/result.csv"
}
```

---

## Rule Management Endpoints

### Base URL

```
/api/v1/rules
```

### 1. Create a Rule Set

**POST** `/api/v1/rules`

Create a new reusable rule set.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Name of the rule set |
| `rules` | array | ✅ | Array of validation rules |
| `description` | string | ❌ | Description of the rule set |

#### Example

```json
{
  "name": "Customer Validation Rules",
  "rules": [
    {
      "rule_name": "Required Email",
      "type": "required",
      "params": {
        "columns": ["email"]
      },
      "error_message": "Email is required"
    }
  ],
  "description": "Rules for validating customer data imports"
}
```

#### Response

```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Customer Validation Rules",
    "rules": [...],
    "description": "...",
    "status": "active"
  },
  "message": "Created successfully"
}
```

---

### 2. Get All Rule Sets

**GET** `/api/v1/rules`

Retrieve all saved rule sets.

#### Response

```json
{
  "success": true,
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Customer Validation Rules",
      "rules": [...],
      "status": "active"
    }
  ],
  "message": ""
}
```

---

### 3. Update a Rule Set

**PUT** `/api/v1/rules/update/{rule_set_id}`

Update rules in an existing rule set.

#### Path Parameters

- `rule_set_id` (string): The ID of the rule set to update

#### Request Body

```json
{
  "rules": [
    {
      "rule_name": "Updated Rule",
      "type": "required",
      "params": {"columns": ["id"]}
    }
  ]
}
```

---

### 4. Delete Rule Sets

**POST** `/api/v1/rules/delete`

Delete multiple rule sets by their IDs.

#### Request Body

```json
{
  "ids": ["id1", "id2", "id3"]
}
```

---

### 5. Get Keywords

**GET** `/api/v1/rules/keywords`

Get keyword mappings for rule templates (used for auto-matching).

#### Response

```json
{
  "description": "Keywords mapping summary.",
  "rules": [
    {
      "rule_id": "...",
      "rule_name": "Validate Email",
      "keywords": ["email", "user_email", "contact_mail"]
    }
  ]
}
```

---

### 6. Match Headers to Rules

**POST** `/api/v1/rules/match`

Auto-suggest rules based on column headers.

#### Request Body

```json
{
  "headers": ["customer_email", "total_order", "customer_level"]
}
```

#### Response

```json
{
  "rule_templates": [
    {
      "rule_name": "Validate Email (customer_email)",
      "type": "expression",
      "params": {
        "columns": ["customer_email"],
        "expression": "pl.col('customer_email').str.contains(r'^[a-zA-Z0-9._%+-]+@')"
      }
    }
  ],
  "descriptions": ["customer_email must be a valid email address"]
}
```

---

## Supported Validation Rule Types

### 1. `required` - Check for Non-Empty Values (Convenience Type)

Validates that specified columns are not null or empty.

> **Note:** This is a convenience type. It's recommended to use `expression` instead for consistency:
> ```json
> "expression": "pl.col('email').is_not_null() & (pl.col('email').cast(pl.Utf8).str.strip_chars() != '')"
> ```

```json
{
  "rule_name": "Required Fields",
  "type": "required",
  "params": {
    "columns": ["id", "name", "email"]
  },
  "error_message": "Field cannot be empty"
}
```

---

### 2. `unique` - Check for Unique Values

Validates that specified columns contain unique values (no duplicates).

```json
{
  "rule_name": "Unique ID",
  "type": "unique",
  "params": {
    "columns": ["id"]
  },
  "error_message": "Duplicate value found"
}
```

---

### 3. `set_unique` - Check Composite-Key Uniqueness

Validates that the combination of values across the specified columns is unique.

- `params.columns` must be a non-empty array of column names.
- Null values participate in the composite-key comparison.
- If any referenced column is missing from the uploaded file, validation fails with an error.

```json
{
  "rule_name": "Unique Full Name",
  "type": "set_unique",
  "params": {
    "columns": ["first_name", "last_name"]
  },
  "error_message": "Duplicate full name found"
}
```

---

### 4. `expression` - Custom Polars Expression

Use custom Polars expressions for complex validation logic. The expression should return `True` for valid rows and `False` for invalid rows.

#### Basic Expression Examples

**Email Format Validation:**
```json
{
  "rule_name": "Valid Email Format",
  "type": "expression",
  "params": {
    "columns": ["email"],
    "expression": "pl.col('email').str.contains(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')"
  },
  "error_message": "Invalid email format"
}
```

**Numeric Range Validation:**
```json
{
  "rule_name": "Valid Age Range",
  "type": "expression",
  "params": {
    "columns": ["age"],
    "expression": "(pl.col('age').cast(pl.Int32) >= 0) & (pl.col('age').cast(pl.Int32) <= 120)"
  },
  "error_message": "Age must be between 0 and 120"
}
```

**Conditional Logic:**
```json
{
  "rule_name": "VIP Customer Logic",
  "type": "expression",
  "params": {
    "columns": ["total_order", "customer_level"],
    "expression": "pl.when(pl.col('total_order').cast(pl.Float64) > 1000).then(pl.col('customer_level') == 'VIP').otherwise(True)"
  },
  "error_message": "Customers with orders > 1000 must be VIP tier"
}
```

**String Length Validation:**
```json
{
  "rule_name": "Phone Number Length",
  "type": "expression",
  "params": {
    "columns": ["phone"],
    "expression": "pl.col('phone').str.len_chars() == 10"
  },
  "error_message": "Phone number must be exactly 10 digits"
}
```

---

## Expression Syntax Reference

The `expression` rule type uses Polars syntax. Common operations:

| Operation | Syntax | Description |
|-----------|--------|-------------|
| Column reference | `pl.col('column_name')` | Access a column |
| Literal value | `pl.lit('value')` | Create a literal |
| String contains | `pl.col('col').str.contains(r'pattern')` | Regex match |
| String length | `pl.col('col').str.len_chars()` | Character count |
| Cast type | `pl.col('col').cast(pl.Int32)` | Type conversion |
| Numeric comparison | `pl.col('col') > 100` | Greater than |
| Boolean AND | `(expr1) & (expr2)` | Logical AND |
| Boolean OR | `(expr1) \| (expr2)` | Logical OR |
| Conditional | `pl.when(cond).then(val1).otherwise(val2)` | If-then-else |
| Is null | `pl.col('col').is_null()` | Check for null |
| Is not null | `pl.col('col').is_not_null()` | Check not null |

---

## Complete Usage Example

### Step 1: Create a Rule Set

```bash
curl -X POST "http://localhost:8000/api/v1/rules" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Product Import Validation",
    "rules": [
      {
        "rule_name": "Required Product ID",
        "type": "required",
        "params": {"columns": ["product_id"]},
        "error_message": "Product ID is required"
      },
      {
        "rule_name": "Unique SKU",
        "type": "unique", 
        "params": {"columns": ["sku"]},
        "error_message": "SKU must be unique"
      },
      {
        "rule_name": "Valid Price",
        "type": "expression",
        "params": {
          "columns": ["price"],
          "expression": "pl.col(\"price\").cast(pl.Float64) > 0"
        },
        "error_message": "Price must be greater than 0"
      }
    ],
    "description": "Validation rules for product data imports"
  }'
```

### Step 2: Validate a File Using Rule Set ID

```bash
curl -X POST "http://localhost:8000/api/v1/validation" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "550e8400-e29b-41d4-a716-446655440001",
    "file_format": "csv",
    "rule_set_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

### Step 3: Query Validation Results

```bash
curl "http://localhost:8000/api/v1/validation/result/query?file_name=validation_result.csv&\$top=10&\$filter=has_validation_error%20eq%20true"
```

### Step 4: Download Full Results

```bash
curl "http://localhost:8000/api/v1/validation/result/download?file_name=validation_result.csv"
```

---

## Error Handling

All endpoints return standard error responses:

```json
{
  "detail": "Error message describing what went wrong"
}
```

| HTTP Status | Description |
|-------------|-------------|
| 400 | Bad Request - Invalid input or validation failure |
| 404 | Not Found - Rule set or file not found |
| 500 | Internal Server Error - Server-side error |

---

## Notes

- Files are processed in batches for memory efficiency
- Large files are supported through streaming validation
- Result files include detailed error information per row
- OData queries support standard filtering, sorting, and pagination
