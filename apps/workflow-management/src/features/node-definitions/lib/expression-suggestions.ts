/**
 * Builds grouped `{{$...}}` autocomplete suggestions for the node-definition builder,
 * in the shape the shared ExpressionAutocompleteInput consumes (IExprSuggestion).
 *
 * Pure functions — group labels default to English and can be overridden with translated
 * strings by the calling component (so tests stay hook-free while the UI stays i18n).
 */
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import type { ISchemaFieldItem } from "./converters";
import { getWorkerOutputFields } from "./worker-fields";

const TYPE_LABEL: Record<string, string> = {
    string: "Text",
    number: "Number",
    boolean: "Yes/No",
    object: "Object",
    array: "List",
};

/** `{{$custom.<field>}}` for each input-schema field — the node's own form values. */
export function customFieldSuggestions(fields: ISchemaFieldItem[], groupLabel = "Custom inputs"): IExprSuggestion[] {
    return fields
        .filter((f) => f.key)
        .map((f) => ({
            value: `{{$custom.${f.key}}}`,
            label: f.label || f.key,
            description: TYPE_LABEL[f.outputType] ?? f.outputType,
            group: groupLabel,
        }));
}

/** A single hint pointing at the workflow's initial input namespace. */
export function inputHintSuggestion(groupLabel = "Workflow input", description = "Workflow input data"): IExprSuggestion {
    return { value: "{{$input.", label: "$input.<field>", description, group: groupLabel };
}

/** `{{$_output.<field>}}` for each field the worker produces (static registry fallback). */
export function workerOutputSuggestions(workerType?: string, groupLabel = "Worker output"): IExprSuggestion[] {
    return getWorkerOutputFields(workerType).map((f) => ({
        value: `{{$_output.${f.key}}}`,
        label: f.key,
        description: `${f.type} — ${f.description}`,
        group: groupLabel,
    }));
}

/** A field from the selected base worker's catalog `output_schema` (its actual response shape). */
export interface IOutputSchemaField {
    key: string;
    outputType?: string;
    label?: string;
    fieldConfig?: { wrapperProps?: { label?: string }; label?: string;[k: string]: any };
}

/** `{{$_output.<field>}}` built from the base worker's declared response schema. */
export function workerOutputSchemaSuggestions(
    schema: IOutputSchemaField[] | undefined | null,
    groupLabel = "Worker output",
): IExprSuggestion[] {
    if (!schema?.length) return [];
    return schema
        .filter((f) => f.key)
        .map((f) => ({
            value: `{{$_output.${f.key}}}`,
            label: f.fieldConfig?.wrapperProps?.label || f.fieldConfig?.label || f.label || f.key,
            description: f.outputType ? (TYPE_LABEL[f.outputType] ?? f.outputType) : "",
            group: groupLabel,
        }));
}

/** Suggestions for input-mapping / locked-default expressions: custom fields + $input hint. */
export function inputExpressionSuggestions(
    fields: ISchemaFieldItem[],
    labels?: { custom?: string; input?: string; inputDesc?: string },
): IExprSuggestion[] {
    return [
        ...customFieldSuggestions(fields, labels?.custom),
        inputHintSuggestion(labels?.input, labels?.inputDesc),
    ];
}
