import type { OutputType } from "../../../types/schema";
import { FieldControl } from "../../field-control";
import type { PaletteFieldDef, PaletteWrapperDef } from "../types";

interface ControlMeta {
  label: string;
  category: string;
  defaultOutputType: OutputType;
  defaultControlProps?: Record<string, unknown>;
}

export const CONTROL_META: Record<string, ControlMeta> = {
  InputControl: { label: "Text Input", category: "Text", defaultOutputType: "string" },
  PasswordControl: { label: "Password", category: "Text", defaultOutputType: "string" },
  NumberControl: { label: "Number", category: "Text", defaultOutputType: "number" },
  TextareaControl: { label: "Textarea", category: "Text", defaultOutputType: "string" },
  CodeControl: { label: "Code Editor", category: "Advanced", defaultOutputType: "string" },
  SelectControl: { label: "Select", category: "Choice", defaultOutputType: "string", defaultControlProps: { placeholder: "", serverOptions: {} } },
  ComboboxControl: { label: "Combobox", category: "Choice", defaultOutputType: "string", defaultControlProps: { readOnly: false, serverOptions: {} } },
  RadioPopupControl: { label: "Radio Group", category: "Choice", defaultOutputType: "string" },
  CheckBoxControl: { label: "Checkbox", category: "Choice", defaultOutputType: "boolean" },
  SwitchControl: { label: "Switch", category: "Choice", defaultOutputType: "boolean" },
  SelectTableControl: { label: "Select Table", category: "Advanced", defaultOutputType: "string", defaultControlProps: { readOnly: false, serverOptions: {} } },
  UploadControl: { label: "Upload", category: "Advanced", defaultOutputType: "string" },
  ButtonControl: { label: "Button", category: "Advanced", defaultOutputType: "string" },
  DatePickerControl: { label: "Date Picker", category: "Date & Time", defaultOutputType: "string", defaultControlProps: { placeholder: "" } },
  DateTimePickerControl: { label: "Date Time Picker", category: "Date & Time", defaultOutputType: "string", defaultControlProps: { timeMode: "24h", placeholder: "" } },
  TimePickerControl: { label: "Time Picker", category: "Date & Time", defaultOutputType: "string", defaultControlProps: { mode: "24h", placeholder: "" } },
  FormBuilderControl: { label: "Form Builder", category: "Advanced", defaultOutputType: "string" },
};

const FALLBACK_META: ControlMeta = { label: "Field", category: "Advanced", defaultOutputType: "string" };

export const FIELD_CATEGORIES = ["Text", "Choice", "Date & Time", "Advanced"] as const;

export const FIELD_DEFS: PaletteFieldDef[] = Object.keys(FieldControl).map((key) => {
  const meta = CONTROL_META[key] ?? FALLBACK_META;
  return {
    kind: "field",
    type: key,
    label: meta.label,
    category: meta.category,
    defaultOutputType: meta.defaultOutputType,
    defaultWrapper: "FormItemWrapper",
    defaultControlProps: meta.defaultControlProps,
  };
});

export const WRAPPER_DEFS: PaletteWrapperDef[] = [
  {
    kind: "wrapper",
    wrapperType: "OdataWrapper",
    label: "OData Table",
    description: "Server-driven table of repeating rows.",
    isContainer: true,
    containerMode: "table",
    defaultOutputType: "array",
  },
  {
    kind: "wrapper",
    wrapperType: "TabWrapper",
    label: "Tabs",
    description: "Group fields into tabbed slots.",
    isContainer: true,
    containerMode: "slots",
    defaultOutputType: "object",
  },
];

export const OUTPUT_TYPE_COLORS: Record<string, string> = {
  string: "bg-blue-100 text-blue-700",
  number: "bg-green-100 text-green-700",
  boolean: "bg-teal-100 text-teal-700",
  array: "bg-orange-100 text-orange-700",
  object: "bg-violet-100 text-violet-700",
};