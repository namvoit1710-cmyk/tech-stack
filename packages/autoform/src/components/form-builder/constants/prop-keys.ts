export const CONTROL_PROP_KEYS: Record<string, string[]> = {
  InputControl: ["placeholder"],
  PasswordControl: ["placeholder"],
  NumberControl: ["min", "max", "readonly"],
  TextareaControl: ["placeholder", "rows"],
  CodeControl: ["language", "languageField", "placeholder", "readOnly", "minHeight", "maxHeight", "dark"],
  SelectControl: ["placeholder", "valueKey", "labelKey", "serverOptions", "options"],
  ComboboxControl: ["placeholder", "valueKey", "labelKey", "multiple", "serverOptions", "options"],
  RadioPopupControl: ["valueKey", "labelKey", "options"],
  CheckBoxControl: ["label"],
  SwitchControl: ["label"],
  SelectTableControl: ["placeholder", "valueKey", "labelKey", "multiple", "selectAction", "serverOptions"],
  UploadControl: ["readOnly"],
  ButtonControl: ["label", "variant", "size", "className", "icon"],
  DatePickerControl: ["placeholder", "mode"],
  DateTimePickerControl: ["placeholder", "mode", "timeMode", "minDateTime", "maxDateTime"],
  TimePickerControl: ["placeholder", "mode", "minTime", "maxTime"],
};

export type PropType =
  | "string"
  | "number"
  | "boolean"
  | "enum"
  | "json"
  // Tier 4 (SA-2036 / SA-2038 / SA-2040): declared now so PropSchema and the widget-registry
  // are a stable shared surface; their widgets land with those tickets.
  | "object"
  | "keyValue"
  | "list"
  | "options"
  // SA-2038: Date/Time control bounds & default value widgets (single scalar strings —
  // ISO date, UTC datetime, or HH:MM:SS).
  | "date"
  | "datetime"
  | "time"
  // SA-2040: Advanced controls — sibling field-key picker (Code languageField, Button watchField)
  // + lucide icon picker (Button icon).
  | "field-ref"
  | "icon";

export interface PropSchema {
  key: string;
  type: PropType;
  /** Human label; falls back to `key`. */
  label?: string;
  /** enum only — the complete set of legal values. */
  options?: string[];
  /** The value the component assumes when the prop is absent. */
  default?: unknown;
  help?: string;
  /** `object` only — the nested sub-schema this group renders (makes PropSchema recursive). */
  fields?: PropSchema[];
}

/** Typed prop schema for container wrappers, keyed by wrapperType. */
export const CONTAINER_WRAPPER_PROP_SCHEMA: Record<string, PropSchema[]> = {
  OdataWrapper: [
    { key: "isAdd", type: "boolean", label: "Add row", default: true },
    { key: "isEdit", type: "boolean", label: "Edit row", default: true },
    { key: "isDelete", type: "boolean", label: "Delete row", default: true },
    { key: "isShowSearch", type: "boolean", label: "Search", default: true },
    { key: "isShowColumnSetting", type: "boolean", label: "Column setting", default: true },
    { key: "readonly", type: "boolean", label: "Read-only", default: false },
    {
      key: "selectionMode",
      type: "enum",
      label: "Selection mode",
      options: ["single", "multiple"],
      help: "Leave unset to keep row checkboxes without publishing a `selected` output.",
    },
  ],
  // Every key below is read by table-wrapper.tsx (line refs are to that file).
  // `callback` / `callApi` (85, 116) are deliberately absent: they are function-valued
  // and wrapperProps is authored as JSON, so no panel control could produce them.
  TableWrapper: [
    { key: "readonly", type: "boolean", label: "Read-only", default: false, help: "Line 37 — also forces off add/delete/selection." },
    { key: "data", type: "json", label: "Data", help: "Lines 77-83 — array of row objects; resets the table when it changes. Absent = use the form value." },
    { key: "rowCount", type: "number", label: "Row count", help: "Lines 70, 78, 86-88 — total rows for server-side pagination. Absent = length of the current data." },
    { key: "isAdd", type: "boolean", label: "Add row", default: false, help: "Line 38." },
    { key: "isDelete", type: "boolean", label: "Delete row", default: false, help: "Line 39." },
    { key: "enableSelection", type: "boolean", label: "Row selection", default: true, help: "Line 202." },
    { key: "pageSize", type: "number", label: "Page size", default: 20, help: "Line 71 → use-table-data (initialPageSize ?? 20)." },
    { key: "pageIndex", type: "number", label: "Page index", default: 0, help: "Line 72 → use-table-data (initialPageIndex = 0)." },
    { key: "search", type: "json", label: "Search config", help: "Line 94 — search config passed to useTableSearch." },
    {
      key: "enablePagination",
      type: "boolean",
      label: "Pagination",
      help: "Line 89 — derived when unset: auto-enables once rowCount exceeds pageSize. No static default.",
    },
    {
      key: "isSyncServer",
      type: "boolean",
      label: "Server-side paging",
      help: "Line 85 — derived when unset: true if a callback/callApi is supplied. No static default.",
    },
  ],
  // ui/tabs.tsx:31-37 (tabsListVariants) and tab-wrapper.tsx:40 are the source of truth.
  // `className` is NOT here: tab-wrapper.tsx:39 hardcodes it on <Tabs> and never reads
  // wrapperProps.className. (TabItemWrapper does read it — different wrapper, later task.)
  TabWrapper: [
    { key: "variant", type: "enum", label: "Variant", options: ["default", "line"], default: "default" },
    { key: "orientation", type: "enum", label: "Orientation", options: ["horizontal", "vertical"], default: "horizontal" },
  ],
  FragmentWrapper: [],
};

/**
 * Typed prop schema for field CONTROLS, keyed by `fieldControl`. Each key is a prop the control
 * actually honours (verified against the primitive it renders). Casing is per-control:
 * NumberControl reads lowercase `readonly`; Input/Textarea/Password take native `readOnly`.
 * Dead props are deliberately absent: `type`/`pattern` (validation), `rows` (Textarea hardcodes
 * field-sizing), `step` (NumberInput is a text input), `autoComplete`/`inputMode` (niche).
 */
export const CONTROL_PROP_SCHEMA: Record<string, PropSchema[]> = {
  InputControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    {
      key: "maxLength",
      type: "number",
      label: "Max length",
      help: "Hard cap — blocks typing past N with NO message. For a validation error, use a Validation rule instead.",
    },
    { key: "minLength", type: "number", label: "Min length" },
    { key: "readOnly", type: "boolean", label: "Read-only", default: false },
    {
      key: "className",
      type: "string",
      label: "CSS class",
      help: "Tailwind class(es), e.g. `text-right font-mono`.",
    },
  ],
  TextareaControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "maxLength", type: "number", label: "Max length", help: "Hard cap — blocks typing past N with no message." },
    { key: "minLength", type: "number", label: "Min length" },
    { key: "readOnly", type: "boolean", label: "Read-only", default: false },
    { key: "className", type: "string", label: "CSS class", help: "Tailwind class(es)." },
  ],
  PasswordControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "readOnly", type: "boolean", label: "Read-only", default: false },
    { key: "className", type: "string", label: "CSS class", help: "Tailwind class(es)." },
  ],
  NumberControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "min", type: "number", label: "Min value", help: "Clamped on blur." },
    { key: "max", type: "number", label: "Max value", help: "Clamped on blur." },
    { key: "readonly", type: "boolean", label: "Read-only", default: false }, // lowercase — NumberControl reads `readonly`
    { key: "className", type: "string", label: "CSS class", help: "Tailwind class(es)." },
  ],
  CheckBoxControl: [{ key: "label", type: "string", label: "Checkbox label" }],
  SwitchControl: [{ key: "label", type: "string", label: "Switch label" }],
  SelectControl: [{ key: "placeholder", type: "string", label: "Placeholder" }],
  ComboboxControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "multiple", type: "boolean", label: "Multiple", help: "On → the field outputs an array; sets outputType=array." },
  ],
  RadioPopupControl: [
    { key: "className", type: "string", label: "CSS class", help: "Tailwind class(es)." },
  ],
  SelectTableControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "multiple", type: "boolean", label: "Multiple", help: "On → the field outputs an array; sets outputType=array." },
    { key: "selectAction", type: "string", label: "Select action", help: "Autoform action fired on row-select (payload carries the picked record as rowData)." },
    { key: "readOnly", type: "boolean", label: "Read-only" },
  ],
  // SA-2038: the `mode` KEY COLLISION is deliberate and load-bearing — DatePicker/DateTime
  // `mode` is a SELECTION shape (single|range) while Time `mode` is a FORMAT (12h|24h). Each
  // control pins its own enum here so the panel never offers the wrong set.
  DatePickerControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    {
      key: "mode", type: "enum", options: ["single", "range"], default: "single",
      help: "range → the field outputs {from,to} (sets outputType=object).",
    },
  ],
  DateTimePickerControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    {
      key: "mode", type: "enum", options: ["single", "range"], default: "single",
      help: "range → the field outputs {from,to} (sets outputType=object).",
    },
    { key: "timeMode", type: "enum", options: ["12h", "24h"], default: "24h" },
    { key: "minDateTime", type: "datetime", label: "Min date-time" },
    { key: "maxDateTime", type: "datetime", label: "Max date-time" },
  ],
  TimePickerControl: [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "mode", type: "enum", options: ["12h", "24h"], default: "24h", label: "Time format" },
    { key: "minTime", type: "time", label: "Min time" },
    { key: "maxTime", type: "time", label: "Max time" },
  ],
  // SA-2040 — Advanced controls. Every key is honoured by its control (grounded):
  // Code → LdcCodeEditorProps/ICodeControlProps; Upload → @ldc/ui <Upload> `...rest` + own readOnly;
  // Button → IButtonControlProps (cosmetic + action-DSL).
  CodeControl: [
    { key: "language", type: "string", label: "Language", help: "Monaco language id, e.g. json / javascript / sql." },
    { key: "languageField", type: "field-ref", label: "Language from field", help: "Sibling field whose value sets the language." },
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "readOnly", type: "boolean", label: "Read-only", default: false },
    { key: "minHeight", type: "number", label: "Min height (px)", default: 160 },
    { key: "maxHeight", type: "number", label: "Max height (px)", default: 480 },
    { key: "dark", type: "enum", label: "Theme", options: ["dark", "light"], help: "Not set = follow the app theme." },
  ],
  UploadControl: [
    { key: "accept", type: "string", label: "Accept", help: "MIME/extension filter, e.g. image/* or .pdf,.docx." },
    { key: "maxSize", type: "number", label: "Max size (bytes)", default: 4194304 },
    { key: "readOnly", type: "boolean", label: "Read-only", default: false },
    { key: "multiple", type: "boolean", label: "Multiple", help: "On → bulk upload; outputs an array of file_ids (see R5)." },
  ],
  ButtonControl: [
    // cosmetic
    { key: "label", type: "string", label: "Label" },
    { key: "variant", type: "enum", label: "Variant", options: ["default", "destructive", "outline", "secondary", "ghost", "link"] },
    { key: "size", type: "enum", label: "Size", options: ["default", "sm", "lg", "icon"] },
    { key: "icon", type: "icon", label: "Icon", help: "lucide icon name." },
    // action-DSL
    { key: "action", type: "string", label: "Action", help: "Action name fired via onFormActions." },
    { key: "actionPayload", type: "keyValue", label: "Action payload" },
    { key: "confirmBefore", type: "boolean", label: "Confirm before", default: false },
    { key: "confirmMessage", type: "string", label: "Confirm message" },
    { key: "loadingOnAction", type: "boolean", label: "Loading on action", default: false },
    { key: "watchField", type: "field-ref", label: "Selected when field" },
    { key: "watchValue", type: "json", label: "…equals value" },
  ],
};

export function resolveControlPropSchema(fieldControl: string | undefined): PropSchema[] {
  if (!fieldControl) return [];
  return CONTROL_PROP_SCHEMA[fieldControl] ?? [];
}

/** wrapperProps suggestions for DesignerField nodes; depends on parent container. */
export const FIELD_WRAPPER_PROP_KEYS: Record<string, string[]> = {
  FormItemWrapper: ["label", "description"],
  FormItemWrapper_odata: ["label", "size", "minSize", "align", "fixed", "hidden"],
  FormItemWrapper_table: ["label", "size", "minSize", "fixed"],
};

/** Slot names a field can occupy when nested inside an OdataWrapper. */
export const ODATA_SLOT_NAMES = ["filter", "more-action", "row-more-action"] as const;

export function resolveControlPropKeys(fieldControl: string | undefined): string[] {
  if (!fieldControl) return [];
  const schema = CONTROL_PROP_SCHEMA[fieldControl];
  if (schema) return schema.map((p) => p.key);
  return CONTROL_PROP_KEYS[fieldControl] ?? [];
}

export function resolveContainerWrapperPropSchema(wrapperType: string | undefined): PropSchema[] {
  if (!wrapperType) return [];
  return CONTAINER_WRAPPER_PROP_SCHEMA[wrapperType] ?? [];
}

export function resolveContainerWrapperPropKeys(wrapperType: string | undefined): string[] {
  return resolveContainerWrapperPropSchema(wrapperType).map((p) => p.key);
}

/** Resolve field wrapperProps suggestions from the parent container's wrapperType. */
export function resolveFieldWrapperPropKeys(parentWrapperType: string | null): string[] {
  if (parentWrapperType === "OdataWrapper") return FIELD_WRAPPER_PROP_KEYS.FormItemWrapper_odata ?? [];
  if (parentWrapperType === "TableWrapper") return FIELD_WRAPPER_PROP_KEYS.FormItemWrapper_table ?? [];
  return FIELD_WRAPPER_PROP_KEYS.FormItemWrapper ?? [];
}
