# Row Transformation API Documentation

## Overview
The Row Transformation API allows you to perform insert, delete, and update operations on CSV/JSON files stored in cloud storage. This API is designed for UI integration and provides flexible row identification methods.

**Base URL:** `http://localhost:8000/api/v1/transformation/rows`

**Version:** 1.0.0

**Last Updated:** March 5, 2026

## File Upload Flow (IMPORTANT)

### Step 1: Upload File to File Service
Before using row transformations, files must be uploaded to the file service:

```javascript
// Upload file
const formData = new FormData();
formData.append('file', fileInput.files[0]);

const uploadResponse = await fetch('/api/v1/files/upload', {
  method: 'POST',
  body: formData
});

const uploadResult = await uploadResponse.json();
// Returns: {"file_id": "abc123-def456-ghi789", "file_url": "..."}
```

### Step 2: Use File ID for Transformations
Use the `file_id` from the upload response as the `file_path` parameter:

```javascript
const transformResponse = await fetch('/api/v1/transformation/rows', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    "file_path": uploadResult.file_id,  // ← Use file_id here!
    "operations": [...]
  })
});
```

**❗ Critical:** Always use the `file_id` returned from file upload, never a file path or URL!

## Table of Contents
1. [Authentication](#authentication)
2. [Request Format](#request-format)
3. [Response Format](#response-format)
4. [Operation Types](#operation-types)
5. [Row Identifier Types](#row-identifier-types)
6. [Examples](#examples)
7. [Error Handling](#error-handling)
8. [UI Integration](#ui-integration)
9. [API Reference](#api-reference)

## Authentication
Currently no authentication required for development. In production, add appropriate authentication headers.

## Request Format

### HTTP Method
```
POST /api/v1/transformation/rows
```

### Headers
```
Content-Type: application/json
Accept: application/json
```

### Request Body Schema
```json
{
  "file_path": "string",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert" | "delete" | "update",
      "row_identifier": {
        "type": "index" | "condition" | "values",
        "index": number,
        "expression": "string",
        "match_values": {
          "column_name": "value"
        }
      },
      "data": {
        "column_name": "value"
      },
      "allow_multiple": false,
      "position": "before" | "after" | "at_index",
      "use_column_indices": boolean
    }
  ],
  "use_column_indices": boolean
}
```

### File Path Parameter (IMPORTANT)
**The `file_path` parameter is the FILE ID returned by the file upload service, NOT a file path!**

**Example:**
1. User uploads file to file service
2. File service returns: `{"file_id": "abc123-def456-ghi789"}`
3. Use this ID as `file_path`: `"file_path": "abc123-def456-ghi789"`

**❗ Critical Note:** Always use the file ID from the upload response, never a local file path or URL.

## Response Format

### Success Response (200)
```json
{
  "success": true,
  "affected_rows": 1,
  "total_rows": 20,
  "message": "Row transformations completed successfully. Original rows: 19, Final rows: 20",
  "preview_data": [
    {
      "id": 123,
      "name": "John Doe",
      "email": "john@example.com",
      "age": 30
    }
  ],
  "download_url": "https://storage.example.com/result.csv?token=..."
}
```

### Error Response (400/500)
```json
{
  "detail": "Row transformation failed: Error message"
}
```

## Operation Types

### 1. INSERT Operations

#### Insert Before Index
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 2
      },
      "data": {
        "name": "New User",
        "email": "new@example.com",
        "age": 25
      },
      "position": "before"
    }
  ]
}
```

#### Insert After Index
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 0
      },
      "data": {
        "name": "First Row",
        "email": "first@example.com"
      },
      "position": "after"
    }
  ]
}
```

#### Insert at Specific Position
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 5
      },
      "data": {
        "name": "Middle Row",
        "email": "middle@example.com"
      },
      "position": "at_index"
    }
  ]
}
```

#### Insert with Column Indices
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 1
      },
      "data": {
        "0": 999,
        "1": "Indexed Name",
        "2": "email@test.com"
      },
      "position": "before",
      "use_column_indices": true
    }
  ]
}
```

### 2. DELETE Operations

#### Delete by Index
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "index",
        "index": 3
      }
    }
  ]
}
```

### 3. UPDATE Operations

#### Update by Index
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "update",
      "row_identifier": {
        "type": "index",
        "index": 3
      },
      "data": {
        "status": "active",
        "score": 95
      }
    }
  ]
}
```

#### Bulk Update by Condition (Explicit Opt-in)
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "update",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('Country') == 'VN'"
      },
      "allow_multiple": true,
      "data": {
        "status": "approved"
      }
    }
  ]
}
```

> Update is partial: only columns in `data` are changed; all other columns remain unchanged.
> By default (`allow_multiple=false`), update requires exactly one matched row.

#### Update by Exact Values
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "update",
      "row_identifier": {
        "type": "values",
        "match_values": {
          "id": 123,
          "name": "John Doe"
        }
      },
      "data": {
        "email": "john.doe@newmail.com",
        "age": 31
      }
    }
  ]
}
```

#### Delete by Condition
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "condition",
        "expression": "pl.col('age') > 65"
      }
    }
  ]
}
```

#### Delete by Exact Values
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "delete",
      "row_identifier": {
        "type": "values",
        "match_values": {
          "id": 123,
          "name": "John Doe"
        }
      }
    }
  ]
}
```

## Row Identifier Types

### 1. Index-based
- **Use**: When you know the exact row position
- **Parameters**: `index` (0-based)
- **Example**: `"index": 5` (6th row)

### 2. Condition-based
- **Use**: When you want to match rows based on column values/expressions
- **Parameters**: `expression` (Polars expression syntax)
- **Examples**:
  - `"expression": "pl.col('age') > 30"`
  - `"expression": "pl.col('status') == 'active'"`
  - `"expression": "pl.col('name').str.contains('John')"`

### 3. Values-based
- **Use**: When you want to match exact values across multiple columns
- **Parameters**: `match_values` (object with column-value pairs)
- **Example**: `{"id": 123, "name": "John Doe"}`

## Examples

### Multiple Operations in One Request
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
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
      "data": {
        "name": "New Active User",
        "status": "active"
      },
      "position": "before"
    }
  ]
}
```

### Insert with Partial Data
```json
{
  "file_path": "abc123-def456-ghi789",
  "file_format": "csv",
  "operations": [
    {
      "operation": "insert",
      "row_identifier": {
        "type": "index",
        "index": 3
      },
      "data": {
        "name": "Partial Data",
        "email": "partial@example.com",
        "age": 28
      },
      "position": "before"
    }
  ]
}
```

## Error Handling

### Common Error Codes
- **400**: Invalid input data, missing required fields
- **404**: File not found
- **500**: Server errors, transformation failures

### Error Response Examples
```json
{
  "detail": "Row transformation failed: Index 10 is out of bounds for dataframe with 5 rows"
}
```

```json
{
  "detail": "Invalid input: At least one operation is required"
}
```

## UI Integration

### JavaScript Fetch Example
```javascript
async function uploadAndTransformFile(file) {
  // Step 1: Upload file
  const formData = new FormData();
  formData.append('file', file);
  
  const uploadResponse = await fetch('/api/v1/files/upload', {
    method: 'POST',
    body: formData
  });
  
  const uploadResult = await uploadResponse.json();
  const fileId = uploadResult.file_id;
  
  // Step 2: Transform rows using file ID
  const response = await fetch('/api/v1/transformation/rows', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    },
    body: JSON.stringify({
      file_path: fileId,  // ← Use file ID from upload!
      file_format: 'csv',
      operations: [
        {
          operation: 'insert',
          row_identifier: { type: 'index', index: 0 },
          data: { name: 'New User', email: 'new@example.com' },
          position: 'before'
        }
      ]
    })
  });

  const result = await response.json();

  if (result.success) {
    console.log(`Affected ${result.affected_rows} rows`);
    
    // Download result file
    if (result.download_url) {
      window.open(result.download_url, '_blank');
    }
    
    return result;
  } else {
    throw new Error(result.detail || 'Transformation failed');
  }
}
```

### React Hook Example
```jsx
import { useState } from 'react';

function useFileTransformation() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const uploadAndTransform = async (file, operations) => {
    setLoading(true);
    setError(null);

    try {
      // Step 1: Upload file
      const formData = new FormData();
      formData.append('file', file);
      
      const uploadResponse = await fetch('/api/v1/files/upload', {
        method: 'POST',
        body: formData
      });
      
      const uploadResult = await uploadResponse.json();
      const fileId = uploadResult.file_id;
      
      // Step 2: Transform using file ID
      const response = await fetch('/api/v1/transformation/rows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_path: fileId,  // ← Use file ID!
          operations: operations
        })
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Request failed');
      }

      setResult(data);
      return data;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return { uploadAndTransform, loading, error, result };
}
```

### Vue.js Composition API Example
```javascript
import { ref } from 'vue';

export function useFileTransformation() {
  const loading = ref(false);
  const error = ref(null);
  const result = ref(null);

  const uploadAndTransform = async (file, operations) => {
    loading.value = true;
    error.value = null;

    try {
      // Step 1: Upload file
      const formData = new FormData();
      formData.append('file', file);
      
      const uploadResponse = await fetch('/api/v1/files/upload', {
        method: 'POST',
        body: formData
      });
      
      const uploadResult = await uploadResponse.json();
      const fileId = uploadResult.file_id;
      
      // Step 2: Transform using file ID
      const response = await fetch('/api/v1/transformation/rows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_path: fileId,  // ← Use file ID!
          operations: operations
        })
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Request failed');
      }

      result.value = data;
      return data;
    } catch (err) {
      error.value = err.message;
      throw err;
    } finally {
      loading.value = false;
    }
  };

  return {
    uploadAndTransform,
    loading: readonly(loading),
    error: readonly(error),
    result: readonly(result)
  };
}
```

## API Reference

### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | **File ID from upload service** (e.g., "abc123-def456-ghi789") |
| `file_format` | string | No | File format: "csv", "json", "parquet" (default: "csv") |
| `operations` | array | Yes | Array of transformation operations |
| `use_column_indices` | boolean | No | Global flag for using column indices (default: false) |

### Operation Object

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `operation` | string | Yes | "insert" or "delete" |
| `row_identifier` | object | Yes | Row identification criteria |
| `data` | object | Yes (for insert) | Column data for insertion |
| `position` | string | Yes (for insert) | "before", "after", or "at_index" |
| `use_column_indices` | boolean | No | Use column indices for this operation |

### Row Identifier Object

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `type` | string | Yes | "index", "condition", or "values" |
| `index` | number | For "index" type | 0-based row index |
| `expression` | string | For "condition" type | Polars expression |
| `match_values` | object | For "values" type | Column-value pairs |

### Response Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `success` | boolean | Operation success status |
| `affected_rows` | number | Number of rows affected |
| `total_rows` | number | Total rows in result file |
| `message` | string | Human-readable status message |
| `preview_data` | array | First 10 rows of result data |
| `download_url` | string | URL to download result file |

## Data Types & Validation

### Supported Data Types
- **Strings**: `"value"`
- **Numbers**: `123`, `45.67`
- **Booleans**: `true`, `false`
- **Nulls**: `null` (for missing columns)

### Validation Rules
- At least 1 operation required
- Maximum 100 operations per request
- Valid file formats: `csv`, `json`, `parquet`
- Valid positions: `before`, `after`, `at_index`
- Valid operations: `insert`, `delete`

## Rate Limits & Performance

- **Max Operations**: 100 per request
- **File Size**: Depends on cloud storage limits
- **Processing Time**: Varies by file size and operation complexity
- **Concurrent Requests**: Handle appropriately in UI

## Best Practices

1. **Validate Input**: Check required fields before sending requests
2. **Handle Errors**: Show user-friendly error messages
3. **Loading States**: Indicate processing status to users
4. **Download Handling**: Provide clear download options
5. **Batch Operations**: Group related operations in single requests
6. **Column Names**: Prefer column names over indices for maintainability
7. **Error Recovery**: Implement retry logic for transient failures

## Changelog

### Version 1.0.0 (March 5, 2026)
- Initial release
- Support for insert and delete operations
- Three row identification methods: index, condition, values
- Column name and index-based data specification
- Download URL support for result files
- Comprehensive error handling

---

## Support

For questions or issues with this API, please contact the backend development team.

**API Endpoint:** `POST /api/v1/transformation/rows`

**Documentation Version:** 1.0.0

**Last Updated:** March 5, 2026