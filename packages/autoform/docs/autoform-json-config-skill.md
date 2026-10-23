---
name: autoform-json-config
description: Generate JSON config for @ldc/autoform — including ISchema (fields, fieldConfig, wrapperProps, controlProps, rules, slots) and ActionConfig[] (action pipeline steps). Trigger when user requests form schema, action config, validation config, OData table config, or any JSON config related to autoform. Do not use for coding tasks unrelated to config generation.
---

# Autoform JSON Config Generator

This skill guides AI to generate accurate JSON config for the `@ldc/autoform` system. Config consists of 2 parts: **ISchema** (describes UI form) and **ActionConfig[]** (describes business logic pipeline).

---

## 1. ARCHITECTURE OVERVIEW

```
JSON Schema (ISchema)
  └── fields: IField[]
        ├── key: string                    # field name in RHF
        ├── outputType: "string" | "number" | "boolean" | "array" | "object"
        ├── default?: unknown
        └── fieldConfig: IFieldConfig
              ├── fieldWrapper?: string     # outer wrapper component (FormItemWrapper, OdataWrapper, InlineWrapper...)
              ├── wrapperProps?: object      # props passed to wrapper
              ├── fieldControl?: string     # input render component (InputControl, SelectControl, ButtonControl...)
              ├── controlProps?: object     # props passed to control
              ├── condition?: IConditionConfig  # show/hide field condition
              ├── rules?: IValidationRules[]    # validation rules
              └── slot?: string             # inject field into slot name of parent wrapper
```

```
ActionConfig[]
  └── action: string                # action name — matches button's controlProps.action
      ├── label?: string            # debug label
      ├── steps: ActionStep[]       # pipeline steps run sequentially
      └── onError?: ActionStep[]    # pipeline when a step fails
```

### Available Components

**fieldWrapper** (outer wrapper component):

| Name | Purpose |
|------|---------|
| `FragmentWrapper` | No wrapper rendered, only renders children |
| `FormItemWrapper` | Label + description + error message |
| `DivWrapper` | Wraps in a div |
| `TableWrapper` | Inline edit table (legacy) |
| `OdataWrapper` | OData table with server-side pagination/filter |
| `TabWrapper` | Tab container |
| `TabItemWrapper` | Tab item |
| `SequenceWrapper` | Sequence/step layout |
| `InlineWrapper` | Render fields inline (columns) |

**fieldControl** (input render component):

| Name | Purpose |
|------|---------|
| `InputControl` | Text input |
| `NumberControl` | Number input |
| `TextareaControl` | Textarea |
| `MarkdownControl` | Markdown editor |
| `SelectControl` | Select dropdown (static or server options) |
| `ComboBoxControl` | Combobox with search |
| `MultipleComboboxControl` | Multi-select combobox |
| `SwitchControl` | Toggle switch |
| `CheckBoxControl` | Checkbox |
| `RadioPopupControl` | Radio in popup |
| `ItemPickerControl` | Popup to pick item from list |
| `SelectTableControl` | Select row(s) from a paginated table dialog |
| `NodePopupControl` | Node popup |
| `CodeControl` | Code editor |
| `SingleUploadControl` | Upload single file |
| `SingleUploadFileControl` | Upload single file (variant) |
| `SingleUploadFieldMappingControl` | Upload + field mapping |
| `FieldMappingControlBase` | Field mapping base |
| `ButtonControl` | Button that triggers action |

---

## 2. FIELD TYPES AND USAGE

### 2.1 Basic field

```json
{
  "key": "name",
  "outputType": "string",
  "fieldConfig": {
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": { "label": "Name", "description": "Enter full name" },
    "fieldControl": "InputControl",
    "controlProps": { "placeholder": "John Doe" },
    "rules": [
      { "method": "required", "message": "Name is required" }
    ]
  }
}
```

### 2.2 Select with static options

```json
{
  "key": "status",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "SelectControl",
    "controlProps": {
      "options": [
        { "value": "active", "label": "Active" },
        { "value": "inactive", "label": "Inactive" }
      ],
      "placeholder": "Select status"
    }
  }
}
```

### 2.3 Select with server options (lazy load from API)

**`IServerOptionsConfig` interface:**

```typescript
type ServerOptionMode = "default" | "odata";

interface IResponseMapping {
  dataPath?: string;   // Path to extract data array from response (default: "data.items")
  totalPath?: string;  // Path to extract total count for pagination (default: "data.count")
}

interface IODataConfig {
  defaultFilter?: string;     // Default OData $filter expression
  defaultOrderBy?: string;    // Default OData $orderby expression
  defaultSelect?: string;     // Default OData $select fields
  searchEnabled?: boolean;    // Enable OData $search capability
  searchField?: string;       // Field name for contains() search filter (SelectTableControl only, e.g. "CityName")
}

interface IServerOptionsConfig {
  endpoint: string;              // OData/API endpoint
  service: string;               // Registered service name (e.g. "odata", "governance")
  dependencies?: string[];       // Field names that options depend on
  mode?: ServerOptionMode;       // "default" = standard REST, "odata" = OData protocol (default: "default")
  method?: "GET" | "POST";       // HTTP method (default: GET)
  params?: Record<string, string | number | boolean>;  // Static query params
  responseMapping?: IResponseMapping;  // Custom paths to extract data from response
  odataConfig?: IODataConfig;    // OData-specific config (only used when mode: "odata")
}
```

**`mode`** determines how the hook builds request params and parses response:
- `"default"` (default) — standard REST API. Uses `responseMapping` to extract data.
- `"odata"` — OData protocol. Automatically adds `$top`, `$skip`, `$count`, `$filter`, `$orderby`, `$select` params. Uses `odataConfig` for default query settings.

**`odataConfig`** (only applies when `mode: "odata"`):
- `defaultFilter` — pre-applied OData filter, e.g. `"status eq 'active'"`
- `defaultOrderBy` — default sort, e.g. `"name asc"`
- `defaultSelect` — limit returned fields, e.g. `"id,name,status"`
- `searchEnabled` — when `true`, enables `$search` param for free-text search
- `searchField` — *(SelectTableControl only)* field name used in `contains(toLower(...), '...')` filter when user types in the dialog search box, e.g. `"CityName"`. When set, search builds a `$filter` instead of `$search`.

**`responseMapping`** allows customizing how data is extracted from API response:
- `dataPath` (default `"data.items"`) — dot-notation path to data array in response
- `totalPath` (default `"data.count"`) — dot-notation path to total count (used for pagination)
- Uses `getByPath()` to resolve nested values, e.g.: `"data.items"` → `response.data.items`
- If API response has a different structure from default, `responseMapping` **must** be set

**How `useServerOptions` resolves service:**
1. `service.split(".").reduce(...)` — resolves path in services object
2. Finds `resolved.fetch` function → calls `fetch(endpoint, params)`
3. Example: `service: "odata"` → `services.odata.fetch(endpoint, params)`

**`ServiceHandler` interface** (each service has 5 methods):

```typescript
interface ServiceHandler {
  fetch: (endpoint: string, params?: Record<string, any>) => Promise<any>;   // GET
  post: (endpoint: string, data?: any, headers?: Record<string, string>) => Promise<any>;
  put: (endpoint: string, data?: any, headers?: Record<string, string>) => Promise<any>;
  patch: (endpoint: string, data?: any, headers?: Record<string, string>) => Promise<any>;
  delete: (endpoint: string, headers?: Record<string, string>) => Promise<any>;
}
```

`useServerOptions` only uses `fetch` (GET). Action engine (`api_call` step) uses the corresponding method: `service.post()`, `service.put()`, etc.

```json
{
  "key": "customerId",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "SelectControl",
    "controlProps": {
      "serverOptions": {
        "service": "odata",
        "endpoint": "/odata/Customers/data",
        "dependencies": []
      },
      "valueKey": "id",
      "labelKey": "name"
    }
  }
}
```

**With dependencies** (value depends on another field):

```json
{
  "key": "city",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "SelectControl",
    "controlProps": {
      "serverOptions": {
        "service": "odata",
        "endpoint": "/odata/Cities/data",
        "dependencies": ["country"]
      },
      "valueKey": "id",
      "labelKey": "name"
    }
  }
}
```

**Dependency behavior:**
- `useWatch` monitors fields listed in `dependencies[]`
- When all dependency fields have values → calls `fetch(endpoint, { country: "VN" })`
- When any dependency field is null/empty → clears options (`setData([])`)
- Dependency values are automatically added to params when calling API

**With `responseMapping`** (API response has different structure from default):

```json
{
  "key": "assignee",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "SelectControl",
    "controlProps": {
      "serverOptions": {
        "service": "governance",
        "endpoint": "/users/list",
        "responseMapping": {
          "dataPath": "data.users",
          "totalPath": "data.total"
        }
      },
      "valueKey": "id",
      "labelKey": "fullName"
    }
  }
}
```

In the example above, the API returns `{ data: { users: [...], total: 50 } }` instead of default `{ data: { items: [...], count: 50 } }`, so `responseMapping` must be set for the hook to extract data correctly.

**With `mode: "odata"`** (OData protocol with built-in filtering/sorting):

```json
{
  "key": "productId",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "SelectControl",
    "controlProps": {
      "serverOptions": {
        "service": "odata",
        "endpoint": "/odata/Products/data",
        "mode": "odata",
        "odataConfig": {
          "defaultFilter": "status eq 'active'",
          "defaultOrderBy": "name asc",
          "defaultSelect": "id,name,category",
          "searchEnabled": true
        }
      },
      "valueKey": "id",
      "labelKey": "name"
    }
  }
}
```

When `mode: "odata"`, the hook automatically appends OData query params (`$top`, `$skip`, `$count`, `$filter`, `$orderby`, `$select`) to the request. `odataConfig` provides default values for these params.

### 2.3b SelectTableControl — select row(s) from a table dialog

`SelectTableControl` opens a paginated table dialog where the user picks one or multiple rows. On confirm it sets the field value to the selected row object(s) and optionally fires an autoform action.

**`controlProps`:**

| Prop | Type | Default | Description |
|---|---|---|---|
| `options` | `OptionRecord[]` | — | Static row data (used when no `serverOptions`) |
| `serverOptions` | `IServerOptionsConfig` | — | Fetch rows from API (same interface as SelectControl) |
| `valueKey` | `string` | `"id"` | Key on the row object used as unique identifier |
| `labelKey` | `string` | `"value"` | Key on the row object shown in the trigger input |
| `multiple` | `boolean` | `false` | Allow selecting multiple rows |
| `placeholder` | `string` | — | Placeholder text when no value selected |
| `selectAction` | `string` | — | Autoform action name to fire on confirm; payload carries `rowData` (selected record) and `rowId` |
| `readOnly` | `boolean` | — | Prevents opening dialog and clearing — no disabled styling |

**Basic example (static options):**

```json
{
  "key": "city",
  "outputType": "object",
  "fieldConfig": {
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": { "label": "City" },
    "fieldControl": "SelectTableControl",
    "controlProps": {
      "options": [
        { "id": "1", "name": "Ho Chi Minh City", "country": "VN" },
        { "id": "2", "name": "Hanoi", "country": "VN" }
      ],
      "valueKey": "id",
      "labelKey": "name",
      "placeholder": "Select city"
    }
  }
}
```

**With server options + selectAction (auto-fill sibling fields after selection):**

When the user confirms a row selection, `selectAction` fires an autoform action whose payload includes `rowData` (the full selected row). Use `update_row` steps in that action to write values from the selected record into other form fields.

> **Note on primitive value + sibling label:** If an `update_row` step writes a scalar (e.g. `city_id`) to the control's own field key, the trigger displays the sibling field named by `labelKey` as the label (read via `useWatch`). To avoid this, configure `update_row` to **not** write to the control's own field — instead set it to the row object so `labelKey` resolves correctly.

```json
{
  "key": "city_id",
  "outputType": "string",
  "fieldConfig": {
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": { "label": "City" },
    "fieldControl": "SelectTableControl",
    "controlProps": {
      "serverOptions": {
        "service": "odata",
        "endpoint": "/odata/Cities/data",
        "mode": "odata",
        "odataConfig": {
          "searchField": "CityName",
          "defaultOrderBy": "CityName asc"
        }
      },
      "valueKey": "city_id",
      "labelKey": "city_name",
      "placeholder": "Select city",
      "selectAction": "on_city_selected"
    }
  }
}
```

Corresponding action — writes selected row fields into the form:

```json
{
  "action": "on_city_selected",
  "steps": [
    {
      "type": "update_row",
      "data": {
        "city_name": "${rowData.city_name}",
        "ward": "${rowData.ward}",
        "address": "${rowData.address}"
      }
    }
  ]
}
```

**Multi-select example:**

```json
{
  "key": "assignees",
  "outputType": "array",
  "fieldConfig": {
    "fieldWrapper": "FormItemWrapper",
    "wrapperProps": { "label": "Assignees" },
    "fieldControl": "SelectTableControl",
    "controlProps": {
      "multiple": true,
      "serverOptions": {
        "service": "governance",
        "endpoint": "/users/list",
        "responseMapping": { "dataPath": "data.users", "totalPath": "data.total" }
      },
      "valueKey": "id",
      "labelKey": "fullName",
      "placeholder": "Select assignees"
    }
  }
}
```

**`selectAction` payload available in action steps:**

| Variable | Value |
|---|---|
| `rowData` | Full selected row object |
| `rowId` | String form of `row[valueKey]` |

Use `${rowData.fieldName}` in `update_row.data` or `api_call.body` to reference the selected record.

### 2.4 Object field (grouped child fields)

```json
{
  "key": "address",
  "outputType": "object",
  "fieldConfig": {
    "fieldWrapper": "InlineWrapper",
    "wrapperProps": { "columns": 2 }
  },
  "fields": [
    { "key": "street", "outputType": "string", "fieldConfig": { "fieldControl": "InputControl" } },
    { "key": "city", "outputType": "string", "fieldConfig": { "fieldControl": "InputControl" } }
  ]
}
```

### 2.4b Tab layout (TabWrapper + TabItemWrapper)

Both `TabWrapper` and `TabItemWrapper` use `outputType: "object"`. TabWrapper wraps the outer container, each TabItemWrapper represents one tab containing child fields.

```json
{
  "key": "details",
  "outputType": "object",
  "fieldConfig": {
    "fieldWrapper": "TabWrapper",
    "wrapperProps": {}
  },
  "fields": [
    {
      "key": "general",
      "outputType": "object",
      "fieldConfig": {
        "fieldWrapper": "TabItemWrapper",
        "wrapperProps": { "label": "General" }
      },
      "fields": [
        {
          "key": "name",
          "outputType": "string",
          "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "wrapperProps": { "label": "Name" },
            "fieldControl": "InputControl",
            "controlProps": {}
          }
        }
      ]
    },
    {
      "key": "settings",
      "outputType": "object",
      "fieldConfig": {
        "fieldWrapper": "TabItemWrapper",
        "wrapperProps": { "label": "Settings" }
      },
      "fields": [
        {
          "key": "isActive",
          "outputType": "boolean",
          "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "wrapperProps": { "label": "Active" },
            "fieldControl": "SwitchControl",
            "controlProps": {}
          }
        }
      ]
    }
  ]
}
```

### 2.5 Array field — OData Table

```json
{
  "key": "tblProducts",
  "outputType": "array",
  "fieldConfig": {
    "fieldWrapper": "OdataWrapper",
    "wrapperProps": {
      "endpoint": "/odata/Products/data",
      "isAdd": true,
      "isEdit": true,
      "isDelete": true,
      "isShowMoreAction": true,
      "isShowColumnSetting": true,
      "isShowSearch": true,
      "isShowRowMoreAction": true,
      "isReadonly": false,
      "actions": []
    }
  },
  "fields": [
    {
      "key": "row",
      "outputType": "object",
      "fieldConfig": {},
      "fields": [
        {
          "key": "name",
          "outputType": "string",
          "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "wrapperProps": { "label": "Product Name", "size": 250 },
            "fieldControl": "InputControl",
            "controlProps": {},
            "rules": [{ "method": "required", "message": "Required" }]
          }
        },
        {
          "key": "price",
          "outputType": "number",
          "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "wrapperProps": { "label": "Price", "size": 120 },
            "fieldControl": "InputControl",
            "controlProps": { "type": "number" }
          }
        }
      ]
    }
  ]
}
```

**Important:**
- Array field for OData table has `fields[0]` as an object field containing column definitions. Button slot fields sit at the **same level** as the `row` object, inside the array field's `fields` (not outside).
- Fields inside the `row` object **must** use `fieldWrapper: "FormItemWrapper"` — OdataWrapper requires FormItemWrapper to render label, size, and error for each column.

### 2.6 Button — placed anywhere in form or injected into slot

```json
{
  "key": "btnValidate",
  "outputType": "string",
  "fieldConfig": {
    "slot": "filter",
    "fieldControl": "ButtonControl",
    "controlProps": {
      "label": "Validate All",
      "variant": "outline",
      "icon": "CheckCircle",
      "action": "validate_table_value",
      "loadingOnAction": true
    }
  }
}
```

**Button in "row-more-action" slot** (has row context):

```json
{
  "key": "btnValidateRow",
  "outputType": "string",
  "fieldConfig": {
    "slot": "row-more-action",
    "fieldControl": "ButtonControl",
    "controlProps": {
      "label": "Validate",
      "variant": "ghost",
      "className": "w-full justify-start",
      "action": "validate_row_condition_value",
      "loadingOnAction": true
    }
  }
}
```

When a button is in `slot: "row-more-action"`, ButtonControl dispatches payload with `rowData`, `rowId`, `updateRow` — engine automatically receives row context. Buttons in this slot always use `variant: "ghost"` and `className: "w-full justify-start"` to match dropdown menu style.

**Button with selected state** — highlights and shows a check icon when a form field matches a value:

```json
{
  "key": "btnApprove",
  "outputType": "string",
  "fieldConfig": {
    "slot": "filter",
    "fieldControl": "ButtonControl",
    "controlProps": {
      "label": "Approve",
      "icon": "Check",
      "action": "action_approve",
      "loadingOnAction": true,
      "variant": "default",
      "watchField": "status",
      "watchValue": "approve"
    }
  }
}
```

- `watchField` — dot-notation path to the form field to watch (e.g. `"status"`, `"data.id"`, `"table_data.__status"`)
- `watchValue` — value that means "selected"; supports `string`, `number`, `boolean`, `object` (deep equality), or `array` (includes check)
- When matched: the button's `icon` is replaced by a `Check` icon and `data-selected` attribute is set for styling
- Both props are optional — omitting them keeps the original button behavior

### 2.7 Item Picker (popup to pick item from list)

```json
{
  "key": "customerId",
  "outputType": "string",
  "fieldConfig": {
    "fieldControl": "ItemPickerControl",
    "controlProps": {
      "popup": {
        "schemaRef": "customer-picker"
      },
      "mode": "single",
      "addItem": {
        "schemaRef": "customer-create-form"
      },
      "serverOptions": {
        "service": "odata",
        "endpoint": "/odata/Customers/data"
      },
      "valueKey": "id",
      "labelKey": "name"
    }
  }
}
```

---

## 3. SLOT SYSTEM

Slots allow injecting buttons/fields into specific positions within a component. Slots are **hardcoded** in each component's source code — they are **not** declared in `wrapperProps`. Child fields use the `fieldConfig.slot` property to inject into the corresponding slot name.

### Available slots by component

**OdataWrapper** (fieldWrapper):

| Slot name | Position | Row context |
|-----------|----------|-------------|
| `filter` | Toolbar filter area | No |
| `more-action` | Header action menu | No |
| `row-more-action` | "..." menu on each row | Yes (`rowData`, `rowId`, `updateRow`) |

**ItemPickerControl** (fieldControl):

| Slot name | Position | Row context |
|-----------|----------|-------------|
| `header` | Popup header | No |
| `footer` | Popup footer | No |

### How to inject a field into a slot

Simply set `fieldConfig.slot` on the child field — the parent component automatically renders the field in the correct position:

```json
{
  "key": "btnValidateAll",
  "outputType": "string",
  "fieldConfig": {
    "slot": "filter",
    "fieldControl": "ButtonControl",
    "controlProps": { "action": "validate_table_value", "label": "Validate All" }
  }
}
```

```json
{
  "key": "btnValidateRow",
  "outputType": "string",
  "fieldConfig": {
    "slot": "row-more-action",
    "fieldControl": "ButtonControl",
    "controlProps": { "action": "validate_row", "label": "Validate", "variant": "ghost", "className": "w-full justify-start" }
  }
}
```

**Note:** No need to configure `slots` in `wrapperProps`. Slot positions are hardcoded in component source code.

---

## 4. VALIDATION RULES

```json
"rules": [
  { "method": "required", "message": "This field is required" },
  {
    "method": "custom",
    "validate": "value?.every(row => row.data?._validate_status !== 'error')",
    "message": "Please fix all validation errors before submitting"
  }
]
```

Supported `method` values: `required`. Custom validation uses JS expressions.

---

## 5. ACTION CONFIG — STEP TYPES

ActionConfig[] can be placed in `wrapperProps.actions` or passed directly to `useActionEngine`.

### 5.1 All step types

| Step type | Purpose | Requires row context? |
|-----------|---------|----------------------|
| `set_value` | Set value on a form field | No |
| `clear_field` | Clear one or more fields | No |
| `validate` | Validate form or specific fields | No |
| `reset_form` | Reset form, optionally keep fields | No |
| `set_field_error` | Set error on a field (business logic) | No |
| `clear_field_error` | Clear errors on fields | No |
| `api_call` | Call REST API | No (but body can use `rowData`) |
| `update_row` | Update a row in table | Yes |
| `refetch_odata` | Refetch OData data | No |
| `refetch_data` | Refetch data by target | No |
| `poll` | Poll endpoint for async service | No |
| `toast` | Show toast notification | No |
| `confirm` | Show confirm dialog | No |
| `redirect` | Navigate to URL | No |
| `close_dialog` | Close dialog | No |
| `refresh` | Refresh form | No |
| `emit_event` | Emit custom event | No |
| `condition` | Branching logic | No |
| `custom` | Call custom handler (escape hatch) | No |
| `trigger_workflow` | Trigger external workflow + wait for socket | No |

### 5.2 Step type details

#### `api_call`

```json
{
  "type": "api_call",
  "service": "governance",
  "method": "POST",
  "endpoint": "/validate/${rowData.id}",
  "body": {
    "description": "${rowData.description}",
    "item_type": "${rowData.item_type}"
  },
  "resultMapping": {
    "data.total": "totalField"
  },
  "errorMapping": {
    "errors.name": "nameField"
  }
}
```

**Important notes:**
- `endpoint` supports `${...}` templates — interpolated with `formValues`, `rowData`, `lastResult`
- `body` has 3 forms: `"formValues"` (send entire form), `"rowData"` (send row data), or a specific object (supports interpolation)
- `body` object uses `interpolateDeep` — each string value is recursively interpolated
- Use `$ref:` prefix when sending raw object/array: `"$ref:formValues.tblProcess"` (prevents conversion to `"[object Object]"`)
- `service` is the registered service name — engine calls via service handler (with auth headers)
- No `service` → engine uses default `serviceRequest`
- `resultMapping`: maps response paths to form fields
- `errorMapping`: maps error paths to form field errors (only triggers on HTTP error)
- Response is stored in `ctx.lastResult` for subsequent steps

#### `update_row`

```json
{
  "type": "update_row",
  "data": {
    "_validate_status": "success",
    "validated_at": "${new Date().toISOString()}"
  },
  "fromResult": {
    "agent_data.valid": "agent_valid",
    "agent_data.message": "agent_message",
    "agent_data.agent_type": "agent_type"
  }
}
```

**Notes:**
- `data`: static data merged into row, supports `interpolateDeep` (template strings in values)
- `fromResult`: maps from `lastResult` — **key = path to read from response**, **value = field name to write into row**
- Requires row context (`rowData`, `updateRow` from ButtonControl dispatch or `selectAction` payload)
- `updateRow` callback writes to `changeTracker` — row in table updates and RHF becomes dirty
- **In `OdataAddEditModal` (edit form):** `update_row` writes directly to the nested RHF form fields via `setValue`. Useful when a `SelectTableControl` field inside a row edit form fires `selectAction` to auto-fill sibling fields (e.g. pick country → fill city, address). The row is committed to the table on Save, not immediately.

#### `condition`

```json
{
  "type": "condition",
  "expr": "lastResult.status === 'error'",
  "then": [
    { "type": "toast", "message": "Error occurred", "variant": "error" }
  ],
  "else": [
    { "type": "toast", "message": "Success!", "variant": "success" }
  ]
}
```

**Notes:**
- `expr` is a JS expression, context includes `formValues`, `lastResult`, `rowData`, `Math`
- `then` and `else` are sub-pipelines (array of steps), run sequentially
- Can be nested (condition inside then/else)

#### `toast`

```json
{
  "type": "toast",
  "message": "Validation completed for ${rowData.name}",
  "variant": "success"
}
```

Variants: `"success"`, `"error"`, `"warning"`, `"info"`

#### `confirm`

```json
{
  "type": "confirm",
  "title": "Confirm Action",
  "message": "Are you sure you want to assign ${rowData.name}?"
}
```

If user cancels → pipeline stops (returns false).

#### `set_field_error`

```json
{
  "type": "set_field_error",
  "field": "${lastResult.field}",
  "message": "${lastResult.message}"
}
```

Used when API returns 200 OK but business logic needs to set an error on a field. Different from `errorMapping` which only triggers on HTTP errors.

**Note:** Manual errors from `setError()` are cleared when RHF `handleSubmit()` runs validation. To block submit, use a custom validation rule on the field or move submit to the action engine.

#### `clear_field_error`

```json
{ "type": "clear_field_error", "field": "description" }
```

Or clear multiple fields:

```json
{ "type": "clear_field_error", "fields": ["name", "description"] }
```

Pass nothing → clears all errors.

#### `trigger_workflow`

```json
{
  "type": "trigger_workflow",
  "service": "governance",
  "method": "POST",
  "endpoint": "/internal-functions/trigger-workflow",
  "body": {
    "workflow_id": "${formValues.workflowId}",
    "input_data": {},
    "wait_for_completion": false
  },
  "runIdPath": "data.run_id",
  "socketNamespace": "/",
  "socketChannel": "data_chunk",
  "socketEventKeyField": "_event",
  "socketEventKey": "workflow_completed",
  "socketErrorKey": "workflow_error",
  "socketRoom": "run:${lastResult.data.run_id}",
  "timeout": 30000,
  "resultMapping": {
    "data.result_id": "resultField"
  }
}
```

**Flow:** POST API → extract `run_id` → emit `wait` on socket → suspend pipeline → wait for completion event → resume.

**Socket room config (3 ways):**
- `socketRoom`: explicit template — `"run:${lastResult.data.run_id}"`
- `socketRoomPrefix`: prefix + run_id — `"workflow:"` → `"workflow:abc123"`
- Not set → defaults to `"run:${runId}"`

#### `refetch_odata`

```json
{ "type": "refetch_odata" }
```

Calls refetch callback registered by OdataWrapper.

#### `refetch_data`

```json
{
  "type": "refetch_data",
  "target": "tblProcess"
}
```

`target` is the field key — engine looks up the refetch registry to call the correct refetch function. Used when form has multiple tables/data sources that need individual refetching.

#### `redirect`

```json
{
  "type": "redirect",
  "url": "/orders/${lastResult.data.orderId}"
}
```

#### `emit_event`

```json
{
  "type": "emit_event",
  "event": "workflow_updated",
  "payload": {
    "taskId": "${rowData.id}",
    "status": "${lastResult.status}"
  }
}
```

Both `event` and `payload` support interpolation.

#### `poll`

```json
{
  "type": "poll",
  "endpoint": "/jobs/${lastResult.data.jobId}/status",
  "interval": 2000,
  "maxAttempts": 30,
  "until": "data.status === 'completed'"
}
```

#### `set_value`

```json
{
  "type": "set_value",
  "field": "totalPrice",
  "expr": "formValues.items.reduce((s, i) => s + i.qty * i.price, 0)"
}
```

Or copy from another field:

```json
{
  "type": "set_value",
  "field": "billingAddress",
  "from": "shippingAddress"
}
```

Or static value:

```json
{
  "type": "set_value",
  "field": "status",
  "value": "submitted"
}
```

---

## 6. PIPELINE CHAINING AND `lastResult`

Each step can read `lastResult` — the result of the previous step. This is the mechanism for chaining data between steps.

```
api_call → lastResult = API response
  ↓
condition(lastResult.status === 'success')
  ├─ then → update_row(fromResult: lastResult.agent_data.*)
  └─ else → toast(lastResult.message)
```

`trigger_workflow` sets `lastResult = { apiResponse, socketPayload, run_id }`.

---

## 7. onError PIPELINE

```json
{
  "action": "validate_row",
  "steps": [...],
  "onError": [
    {
      "type": "update_row",
      "data": { "_validate_status": "error" }
    },
    {
      "type": "toast",
      "message": "Request failed: ${lastResult.error}",
      "variant": "error"
    }
  ]
}
```

`onError` runs when any step throws an error (HTTP failure, socket timeout, etc). `lastResult = { error: "message" }`.

---

## 8. COMPLETE EXAMPLES

### Example 1: Validate each row — call API, branch 3 cases, update row

```json
{
  "action": "validate_row_condition_value",
  "steps": [
    {
      "type": "api_call",
      "service": "profile-manager-agent",
      "method": "POST",
      "endpoint": "/execute",
      "body": {
        "description": "${rowData.description}"
      }
    },
    {
      "type": "condition",
      "expr": "lastResult.status === 'success'",
      "then": [
        {
          "type": "update_row",
          "data": { "_validate_status": "success" },
          "fromResult": {
            "agent_data.valid": "agent_valid",
            "agent_data.message": "agent_message",
            "agent_data.agent_type": "agent_type"
          }
        },
        {
          "type": "toast",
          "message": "Validation passed",
          "variant": "success"
        }
      ],
      "else": [
        {
          "type": "condition",
          "expr": "lastResult.status === 'warning'",
          "then": [
            {
              "type": "update_row",
              "data": {
                "_validate_status": "warning",
                "_validate_message": "${lastResult.agent_data.message}",
                "_validate_field": "${lastResult.field}"
              }
            },
            {
              "type": "toast",
              "message": "Warning: ${lastResult.agent_data.message}",
              "variant": "warning"
            }
          ],
          "else": [
            {
              "type": "update_row",
              "data": {
                "_validate_status": "error",
                "_validate_message": "${lastResult.agent_data.message}",
                "_validate_field": "${lastResult.field}"
              }
            },
            {
              "type": "set_field_error",
              "field": "${lastResult.field}",
              "message": "${lastResult.message}"
            },
            {
              "type": "toast",
              "message": "Error: ${lastResult.message}",
              "variant": "error"
            }
          ]
        }
      ]
    }
  ],
  "onError": [
    {
      "type": "update_row",
      "data": { "_validate_status": "error", "_validate_message": "${lastResult.error}" }
    },
    {
      "type": "toast",
      "message": "Request failed: ${lastResult.error}",
      "variant": "error"
    }
  ]
}
```

### Example 2: Validate entire table — call API with file_id, refetch

```json
{
  "action": "validate_table_value",
  "steps": [
    {
      "type": "api_call",
      "service": "profile-manager-agent",
      "method": "POST",
      "endpoint": "/execute-batch",
      "body": {
        "file_id": "${formValues.__tblProcess_file_id}"
      }
    },
    {
      "type": "refetch_data",
      "target": "tblProcess"
    },
    {
      "type": "toast",
      "message": "Validation completed",
      "variant": "success"
    }
  ],
  "onError": [
    {
      "type": "toast",
      "message": "Batch validation failed: ${lastResult.error}",
      "variant": "error"
    }
  ]
}
```

### Example 3: Assign task + trigger workflow continuation

```json
{
  "action": "assign_and_continue",
  "steps": [
    {
      "type": "confirm",
      "title": "Assign Task",
      "message": "Assign this task to yourself and continue workflow?"
    },
    {
      "type": "api_call",
      "service": "governance",
      "method": "POST",
      "endpoint": "/tasks/${rowData.id}/assign",
      "body": {
        "assignee": "${formValues.currentUserId}"
      }
    },
    {
      "type": "update_row",
      "fromResult": {
        "data.assignee": "assignee",
        "data.assignee_name": "assignee_name",
        "data.status": "status"
      }
    },
    {
      "type": "trigger_workflow",
      "service": "governance",
      "method": "POST",
      "endpoint": "/internal-functions/trigger-workflow",
      "body": {
        "workflow_id": "${formValues.workflowId}",
        "task_id": "${rowData.id}",
        "input_data": "$ref:lastResult.data"
      },
      "runIdPath": "data.run_id",
      "socketNamespace": "/",
      "socketChannel": "data_chunk",
      "socketEventKeyField": "_event",
      "socketEventKey": "workflow_completed",
      "socketErrorKey": "workflow_error",
      "timeout": 60000
    },
    {
      "type": "refetch_odata"
    },
    {
      "type": "toast",
      "message": "Task assigned and workflow continued",
      "variant": "success"
    }
  ],
  "onError": [
    {
      "type": "toast",
      "message": "Failed: ${lastResult.error}",
      "variant": "error"
    }
  ]
}
```

### Example 4: Create new item — confirm, call API, refetch list

```json
{
  "action": "create_condition_set",
  "steps": [
    {
      "type": "confirm",
      "title": "Create New",
      "message": "Create a new condition set?"
    },
    {
      "type": "api_call",
      "service": "governance",
      "method": "POST",
      "endpoint": "/condition-sets/generate",
      "body": { "source": "runtime" }
    },
    {
      "type": "toast",
      "message": "Service is running...",
      "variant": "info"
    },
    {
      "type": "poll",
      "endpoint": "/jobs/${lastResult.data.jobId}/status",
      "interval": 2000,
      "maxAttempts": 30,
      "until": "data.status === 'completed'"
    },
    {
      "type": "refetch_odata"
    },
    {
      "type": "toast",
      "message": "New condition set created!",
      "variant": "success"
    }
  ]
}
```

### Example 5: Submit form via action engine (instead of RHF handleSubmit)

```json
{
  "action": "submit_form",
  "steps": [
    {
      "type": "validate"
    },
    {
      "type": "condition",
      "expr": "formValues.tblProcess?.some(op => op.data?._validate_status === 'error')",
      "then": [
        {
          "type": "toast",
          "message": "Please fix all validation errors before submitting",
          "variant": "error"
        }
      ],
      "else": [
        {
          "type": "api_call",
          "service": "governance",
          "method": "POST",
          "endpoint": "/submit",
          "body": "formValues"
        },
        {
          "type": "toast",
          "message": "Submitted successfully",
          "variant": "success"
        },
        {
          "type": "redirect",
          "url": "/dashboard"
        }
      ]
    }
  ]
}
```

---

## 9. COMPLETE SCHEMA EXAMPLE — OData Table with actions

```json
{
  "fields": [
    {
      "key": "tblProcess",
      "outputType": "array",
      "fieldConfig": {
        "fieldWrapper": "OdataWrapper",
        "wrapperProps": {
          "endpoint": "/odata/ConditionValues/data",
          "isAdd": false,
          "isEdit": false,
          "isDelete": false,
          "isShowMoreAction": false,
          "isShowColumnSetting": true,
          "isShowSearch": true,
          "isShowRowMoreAction": true,
          "actions": [
            {
              "action": "validate_table_value",
              "steps": [
                {
                  "type": "api_call",
                  "service": "profile-manager-agent",
                  "method": "POST",
                  "endpoint": "/execute-batch",
                  "body": { "file_id": "${formValues.__tblProcess_file_id}" }
                },
                { "type": "refetch_data", "target": "tblProcess" },
                { "type": "toast", "message": "Validation completed", "variant": "success" }
              ]
            },
            {
              "action": "validate_row_condition_value",
              "steps": [
                {
                  "type": "api_call",
                  "service": "profile-manager-agent",
                  "method": "POST",
                  "endpoint": "/execute",
                  "body": { "description": "${rowData.description}" }
                },
                {
                  "type": "update_row",
                  "fromResult": {
                    "agent_data.valid": "agent_valid",
                    "agent_data.message": "agent_message",
                    "agent_data.agent_type": "agent_type"
                  }
                },
                { "type": "toast", "message": "Validation completed", "variant": "success" }
              ],
              "onError": [
                { "type": "toast", "message": "Validation failed: ${lastResult.error}", "variant": "error" }
              ]
            }
          ]
        }
      },
      "fields": [
        {
          "key": "row",
          "outputType": "object",
          "fieldConfig": {},
          "fields": [
            {
              "key": "description",
              "outputType": "string",
              "fieldConfig": {
                "fieldWrapper": "FormItemWrapper",
                "wrapperProps": { "label": "Description", "size": 300 },
                "fieldControl": "InputControl",
                "controlProps": {},
                "rules": [{ "method": "required", "message": "Description is required" }]
              }
            },
            {
              "key": "agent_valid",
              "outputType": "string",
              "fieldConfig": {
                "fieldWrapper": "FormItemWrapper",
                "wrapperProps": { "label": "Status", "size": 100 }
              }
            },
            {
              "key": "agent_message",
              "outputType": "string",
              "fieldConfig": {
                "fieldWrapper": "FormItemWrapper",
                "wrapperProps": { "label": "Message", "size": 250 }
              }
            }
          ]
        },
        {
          "key": "btnValidateAll",
          "outputType": "string",
          "fieldConfig": {
            "slot": "filter",
            "fieldControl": "ButtonControl",
            "controlProps": {
              "label": "Validate All",
              "variant": "outline",
              "icon": "CheckCircle",
              "action": "validate_table_value",
              "loadingOnAction": true
            }
          }
        },
        {
          "key": "btnValidateRow",
          "outputType": "string",
          "fieldConfig": {
            "slot": "row-more-action",
            "fieldControl": "ButtonControl",
            "controlProps": {
              "label": "Validate",
              "variant": "ghost",
              "className": "w-full justify-start",
              "action": "validate_row_condition_value",
              "loadingOnAction": true
            }
          }
        }
      ]
    }
  ]
}
```

---

## 10. TEMPLATE INTERPOLATION RULES

### `${...}` syntax

All strings in action config support `${expression}` — replaced with runtime values.

**Available context variables:**
- `formValues` — all form data from RHF
- `rowData` — current row data (only when button is in a row slot)
- `lastResult` — response from previous step
- `Math` — Math object

**Examples:**
- `"/orders/${rowData.id}"` → `"/orders/123"`
- `"Hello ${formValues.name}"` → `"Hello John"`
- `"${lastResult.agent_data.message}"` → `"Validated successfully"`

### `$ref:` prefix

Used when sending raw object/array in body (prevents conversion to `"[object Object]"`):

```json
{
  "body": {
    "file_id": "${formValues.fileId}",
    "operations": "$ref:formValues.tblProcess"
  }
}
```

`"$ref:formValues.tblProcess"` → returns the original array, not converted to string.

### Expressions in `condition.expr`

```json
{ "expr": "lastResult.status === 'error'" }
{ "expr": "formValues.items.length > 0" }
{ "expr": "rowData.status === 'pending' && lastResult.data?.canAssign" }
```

---

## 11. useActionEngine HOOK — APP LEVEL INTEGRATION

```tsx
const { handleFormActions, isWaitingWorkflow } = useActionEngine({
  actions: schema.wrapperProps.actions,   // ActionConfig[]
  builderRef,                             // React.RefObject<BuilderRef>
  actionSocket,                           // SocketClient (optional)
  services: { governance: govSdk, odata: odataSdk },  // Record<string, ServiceHandler>
  customHandlers: {},
  refetchData,
  toast,
  confirm,
  redirect,
  closeDialog,
  refresh,
  onEvent: (event, payload) => { /* handle custom events */ },
});

// Pass to Builder
<Builder
  schema={schema}
  onFormActions={handleFormActions}
  services={services}
  ref={builderRef}
/>
```

### Registered Services

Registered services in the system. Use the **Service name** column value for the `service` field in action steps (`api_call`, `trigger_workflow`, `refetch_data`).

| Service name | Description | Base path |
|---|---|---|
| `governance` | Governance API — manage tasks, rules, workflows | `api/v1` |
| `odata` | File Service — OData queries, file operations | `api/v1` |
| `workflow` | Workflow API — workflow runtime, trigger, status | `api/v1` |
| `profile-manager-agent` | AI Agent — validate, profile management | `api/v1` |
| `intergration-hub` | Integration Hub — connect external systems | `api/v1` |

**Usage example in action step:**

```json
{
  "type": "api_call",
  "service": "governance",
  "method": "POST",
  "endpoint": "/tasks/${rowData.id}/assign"
}
```

Engine auto-resolves URL: `baseUrl + basePath + endpoint`. Service already has `basePath: "api/v1"` so endpoint should **not** include `/api/v1`.

---

## 12. GENERATION RULES — WHEN AI GENERATES CONFIG

1. **Always validate field types** — `outputType` must match actual data
2. **Array field for table** — required structure: `fields[0]` is an object containing column fields. Button slot fields sit at the **same level** as the row object, inside the array field's `fields`
3. **Button slot** — if button needs row context use `slot: "row-more-action"` (OdataWrapper), otherwise use `slot: "filter"` or `slot: "more-action"`. ItemPickerControl has `slot: "header"` and `slot: "footer"`
4. **Action name** — must be unique, must match `controlProps.action` of the button
5. **Templates in body** — use `${...}` for string values, `$ref:` for object/array
6. **onError** — always add `onError` pipeline for actions containing `api_call` or `trigger_workflow`
7. **condition nesting** — limit to 3 levels max, deeper nesting is hard to maintain
8. **fromResult mapping** — key = path to read from response, value = field name to write into row
9. **service name** — must be one of: `governance`, `odata`, `workflow`, `profile-manager-agent`, `intergration-hub` (see Registered Services table in section 11)
10. **endpoint must not include basePath** — service already has `basePath: "api/v1"`, so endpoint only needs the path after basePath. Example: `/tasks/${rowData.id}/assign` (correct), NOT `/api/v1/tasks/${rowData.id}/assign` (wrong)
11. **interpolation scope** — only `formValues`, `rowData`, `lastResult`, `Math` — no DOM access or external state
12. **fieldWrapper name** — must be exact component name: `OdataWrapper` (not `ODataTableWrapper` or `ODataWrapper`)
13. **OdataWrapper row fields** — all fields inside the `row` object **must** use `fieldWrapper: "FormItemWrapper"` to render label, size, and error
14. **TabWrapper / TabItemWrapper** — both use `outputType: "object"`. TabWrapper wraps the outer container, each TabItemWrapper is one tab
15. **SelectTableControl `selectAction`** — the named action must exist in `wrapperProps.actions` of a parent `OdataWrapper`, or be passed to `useActionEngine` at app level. The action receives `rowData` (full selected row) and `rowId` in its payload. Use `update_row` steps to write selected values into sibling form fields.
16. **SelectTableControl `readOnly`** — set via `controlProps.readOnly: true` (not `disabled`). Prevents opening the dialog and clearing the value; does not apply disabled styling so the field remains visually normal.
17. **SelectTableControl `odataConfig.searchField`** — only applies when `mode: "odata"`. When set, the dialog search box builds `$filter=contains(toLower(field), 'term')` instead of `$search=term`. Use when the OData endpoint does not support `$search`.
