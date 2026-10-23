export { FIELD_DEFS, WRAPPER_DEFS, FIELD_CATEGORIES, OUTPUT_TYPE_COLORS } from "./palette";

export const CONTROLS_WITH_OPTIONS = new Set<string>([
    "SelectControl", "ComboboxControl", "SelectTableControl", "RadioPopupControl",
]);

export const OUTPUT_TYPE_OPTIONS: { value: string; label: string }[] = [
    { value: "string", label: "string" },
    { value: "number", label: "number" },
    { value: "boolean", label: "boolean" },
    { value: "array", label: "array" },
    { value: "object", label: "object" },
];

export const OPERATOR_OPTIONS = [
    { value: "equals", label: "equals" },
    { value: "notEquals", label: "not equals" },
    { value: "exists", label: "exists (truthy)" },
    { value: "notExists", label: "not exists" },
    { value: "contains", label: "contains" },
    { value: "in", label: "in (array)" },
    { value: "greaterThan", label: ">" },
    { value: "lessThan", label: "<" },
    { value: "greaterOrEqual", label: ">=" },
    { value: "lessOrEqual", label: "<=" },
];

export type { PropSchema, PropType } from "./prop-keys";
export {
    CONTROL_PROP_KEYS,
    CONTROL_PROP_SCHEMA,
    CONTAINER_WRAPPER_PROP_SCHEMA,
    FIELD_WRAPPER_PROP_KEYS,
    ODATA_SLOT_NAMES,
    resolveControlPropKeys,
    resolveControlPropSchema,
    resolveContainerWrapperPropKeys,
    resolveContainerWrapperPropSchema,
    resolveFieldWrapperPropKeys,
} from "./prop-keys";