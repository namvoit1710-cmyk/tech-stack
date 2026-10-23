import type { IConditionConfig } from "./condition";
import type { IServerOptionsConfig } from "./server-option-config";
import type { IValidationRules } from "./validation";

export type OutputType = "string" | "number" | "boolean" | "array" | "object";

export interface FieldComponentProps {
    field?: IField;
    name: string;
    [key: string]: any;
}

export interface FieldWrapperProps extends FieldComponentProps {
    path?: string[];
    children: React.ReactNode;
}

export type IWrapperComponent = string;
export type IFieldComponent = string;
export interface ISchema {
    fields: IField[];
}

export interface IField {
    key: string;
    outputType: OutputType;
    default?: unknown;
    fieldConfig: IFieldConfig;
    fields?: IField[];
}

export interface IFieldConfig {
    slot?: string;
    fieldWrapper?: string;
    wrapperProps?: Record<string, any>;
    fieldControl?: string;
    controlProps?: Record<string, any> & { serverOptions?: IServerOptionsConfig };
    condition?: IConditionConfig;
    rules?: IValidationRules[];
    /**
     * Inline JS attached to a control's native events.
     * Key = event name (e.g. "onChange", "onClick"); value = full async
     * function source, named after the event: `async function onChange(ctx) { … }`.
     * Executed in-browser by run-field-script.ts with the ActionScriptCtx SDK.
     */
    script?: Record<string, string>;
}

export interface IButtonAction {
    action: string;
    label: string;

    [key: string]: unknown;
}
