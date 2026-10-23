/**
 * Pure converters between the builder's friendly state shapes and the backend
 * AutoFormField / dict shapes. Single home for logic previously scattered across
 * schema-field-list, locked-defaults-editor, input-mapping-editor, key-value-editor.
 *
 * All functions here are pure (no React) so they are trivially unit-testable —
 * see converters.test.ts for the round-trip guarantees.
 */

import type { IWorkflowSchemaField } from "@/features/workflows/types/workflows";
import type { INodeInputSchema } from "@/features/workflows/types/node-pallete";
import { buildGatewayOptionsServerConfig, parseGatewayOptionsServerConfig } from "./gateway-binding";
import { getWorkerFieldMeta, type IWorkerFieldMeta } from "./worker-fields";

/* ================================================================== */
/*  Input/Output schema  (ISchemaFieldItem[] ↔ AutoFormField[])        */
/* ================================================================== */

export interface ISchemaFieldItem {
    key: string;
    label: string;
    outputType: string;
    fieldControl: string;
    required: boolean;
    placeholder: string;
    description: string;
    options: string;
    /** When set, the control loads its options live from a registered Builder
     *  service instead of the static `options` list (autoform `serverOptions`). */
    serverOptions?: IFieldServerOptions;
}

/** Friendly (form-state) shape of an autoform `controlProps.serverOptions`.
 *  `dependencies` is a comma list here, split to an array on convert. */
export interface IFieldServerOptions {
    /** "manual" = GET a service endpoint; "gateway" = invoke a gateway function. */
    source?: "manual" | "gateway";
    service: string;     // registered Builder service key, e.g. "workflow"
    endpoint: string;    // path under the service, e.g. "gateway/apps"
    valueField: string;  // item field used as the option value (autoform valueKey)
    labelField: string;  // item field used as the option label (autoform labelKey)
    dependencies: string; // comma-separated form field keys → request query params
    // gateway source:
    appName?: string;
    functionName?: string;
    functionId?: string;
    dataPath?: string;   // items path under the invoke response, default "result.body"
}

/** One-click preset: list Gateway apps from the control-plane (`workflow`
 *  service). The gateway list endpoint lives on the same service that hosts the
 *  rest of the control plane, so no new service registration is needed.
 *  ponytail: only "apps" — the functions endpoint is path-templated
 *  (`gateway/apps/{app}/functions`) and serverOptions can't fill a path segment
 *  from a form value, so dynamic "functions of a chosen app" needs a fixed app. */
export const GATEWAY_APPS_SERVER_OPTIONS: IFieldServerOptions = {
    service: "workflow",
    endpoint: "gateway/apps",
    valueField: "name",
    labelField: "name",
    dependencies: "",
};

/** A field is invalid only if it turns on server options but leaves a required
 *  piece blank — otherwise (static field, no server options) it is always valid. */
export function isSchemaFieldValid(f: ISchemaFieldItem): boolean {
    const so = f.serverOptions;
    if (!so) return true;
    if (so.source === "gateway") {
        // The (app, function) pair is the selector now — functionId is no longer sent,
        // so gating on it would reject a perfectly good name-addressed source.
        return !!(so.appName?.trim() && so.functionName?.trim()
            && so.valueField.trim() && so.labelField.trim());
    }
    return !!(so.service.trim() && so.endpoint.trim() && so.valueField.trim() && so.labelField.trim());
}

export const schemaFieldsValid = (fields: ISchemaFieldItem[]): boolean => fields.every(isSchemaFieldValid);

/** Control types offered in the field editor, with humanized labels. */
export const FIELD_CONTROL_OPTIONS = [
    { id: "InputControl", label: "Text Input" },
    { id: "TextareaControl", label: "Text Area" },
    { id: "NumberControl", label: "Number" },
    { id: "SelectControl", label: "Dropdown Select" },
    { id: "ComboBoxControl", label: "Searchable Select" },
    { id: "MultipleComboboxControl", label: "Multi Select" },
    { id: "SwitchControl", label: "Toggle Switch" },
    { id: "CheckBoxControl", label: "Checkbox" },
] as const;

/** Data types offered in the field editor, with humanized labels. */
export const OUTPUT_TYPE_OPTIONS = [
    { id: "string", label: "Text" },
    { id: "number", label: "Number" },
    { id: "boolean", label: "Yes/No" },
    { id: "object", label: "Object" },
    { id: "array", label: "List" },
] as const;

const OPTION_CONTROLS = new Set(["SelectControl", "ComboBoxControl", "MultipleComboboxControl"]);

/** Output types whose value has no fixed option set and is edited as raw JSON. */
const STRUCTURED_TYPES = new Set(["array", "object"]);

/** Controls the field editor can actually render/select — used to keep the
 *  read-back `fieldControl` valid when the persisted shape carries one the UI
 *  doesn't expose (e.g. the synthetic CodeControl we inject for array/object). */
const KNOWN_FIELD_CONTROLS = new Set<string>(FIELD_CONTROL_OPTIONS.map((o) => o.id));

export const createEmptyField = (): ISchemaFieldItem => ({
    key: "",
    label: "",
    outputType: "string",
    fieldControl: "InputControl",
    required: false,
    placeholder: "",
    description: "",
    options: "",
});

export function schemaFieldsToAutoForm(fields: ISchemaFieldItem[]): IWorkflowSchemaField[] {
    return fields
        .filter((f) => f.key)
        .map((f) => {
            const rules: Record<string, any>[] = [];
            if (f.required) rules.push({ method: "required" });

            const controlProps: Record<string, any> = {};
            if (f.placeholder) controlProps.placeholder = f.placeholder;

            const so = OPTION_CONTROLS.has(f.fieldControl) ? f.serverOptions : undefined;
            if (so && isSchemaFieldValid(f)) {
                // Server-driven options win over the static list (they're mutually exclusive).
                if (so.source === "gateway") {
                    controlProps.serverOptions = buildGatewayOptionsServerConfig({
                        appName: so.appName ?? "",
                        functionName: so.functionName ?? "",
                        functionId: so.functionId ?? "",
                        dataPath: so.dataPath ?? "",
                    });
                } else {
                    const dependencies = so.dependencies.split(",").map((d) => d.trim()).filter(Boolean);
                    controlProps.serverOptions = {
                        service: so.service,
                        endpoint: so.endpoint,
                        responseMapping: { dataPath: "data.items", totalPath: "data.total" },
                        ...(dependencies.length ? { dependencies } : {}),
                    };
                }
                controlProps.valueKey = so.valueField;
                controlProps.labelKey = so.labelField;
            } else if (OPTION_CONTROLS.has(f.fieldControl) && f.options) {
                controlProps.options = f.options
                    .split(",")
                    .map((opt) => opt.trim())
                    .filter(Boolean)
                    .map((opt) => ({ id: opt.toLowerCase().replace(/\s+/g, "_"), value: opt }));
            }

            // The backend FormField contract rejects an array/object field unless it
            // declares a nested schema, is readonly, or uses CodeControl — otherwise
            // the field is silently dropped when the node catalog spec is compiled
            // (so the param never shows up in the workflow). Free-form structured
            // values have no fixed option set, so route them through a JSON code
            // editor, which both satisfies the contract and gives a usable input.
            let fieldControl = f.fieldControl;
            if (STRUCTURED_TYPES.has(f.outputType) && !OPTION_CONTROLS.has(f.fieldControl)) {
                fieldControl = "CodeControl";
                controlProps.language = "json";
            }

            return {
                key: f.key,
                outputType: f.outputType,
                fieldConfig: {
                    fieldWrapper: "FormItemWrapper",
                    wrapperProps: {
                        label: f.label || f.key,
                        required: f.required,
                    },
                    fieldControl,
                    controlProps,
                    ...(rules.length > 0 ? { rules } : {}),
                },
            } as unknown as IWorkflowSchemaField;
        });
}

/** Full-width layout (label above control) for the narrow builder preview panels. */
const PREVIEW_FIELD_LAYOUT = {
    labelSpan: "xl-12 lg-12 md-12 xs-12",
    fieldSpan: "xl-12 lg-12 md-12 xs-12",
} as const;

/**
 * Build AutoForm fields for the builder's live-preview ("Inputs" tab) and the
 * "Try it" test form ("Input Mapping" tab). Both must render identically, so they
 * share this transform.
 *
 * array/object fields have no nested item schema here, so render them as a leaf
 * (raw JSON) editor — schemaFieldsToAutoForm already routes them to CodeControl,
 * and we drop the outputType to "string" so the Builder shows that leaf control
 * instead of an empty repeater (matching how the workflow node-detail form renders
 * them). Fields are laid out full-width for the narrow panels.
 */
export function schemaFieldsToPreviewFields(fields: ISchemaFieldItem[]): IWorkflowSchemaField[] {
    return schemaFieldsToAutoForm(fields).map((field) => {
        const f = field as any;
        return {
            ...f,
            outputType: f.outputType === "array" || f.outputType === "object" ? "string" : f.outputType,
            fieldConfig: {
                ...f.fieldConfig,
                wrapperProps: { ...f.fieldConfig?.wrapperProps, ...PREVIEW_FIELD_LAYOUT },
                controlProps: { ...f.fieldConfig?.controlProps, className: "w-full" },
            },
        } as IWorkflowSchemaField;
    });
}

export function autoFormToSchemaFields(schema: IWorkflowSchemaField[] | undefined | null): ISchemaFieldItem[] {
    if (!schema?.length) return [];

    return schema.map((field: any) => {
        const fc = field.fieldConfig ?? {};
        const wp = fc.wrapperProps ?? {};
        const cp = fc.controlProps ?? {};

        let options = "";
        if (cp.options && Array.isArray(cp.options)) {
            options = cp.options.map((o: any) => o.value ?? o.id ?? o).join(", ");
        }

        let serverOptions: IFieldServerOptions | undefined;
        const so = cp.serverOptions;
        if (so && typeof so === "object") {
            const gw = parseGatewayOptionsServerConfig(so);
            serverOptions = gw
                ? {
                    source: "gateway",
                    appName: gw.appName,
                    functionName: gw.functionName,
                    functionId: gw.functionId,
                    dataPath: gw.dataPath,
                    service: "",
                    endpoint: "",
                    valueField: cp.valueKey ?? "id",
                    labelField: cp.labelKey ?? "value",
                    dependencies: "",
                }
                : {
                    source: "manual",
                    service: so.service ?? "",
                    endpoint: so.endpoint ?? "",
                    valueField: cp.valueKey ?? "id",
                    labelField: cp.labelKey ?? "value",
                    dependencies: Array.isArray(so.dependencies) ? so.dependencies.join(", ") : "",
                };
        }

        const hasRequired = wp.required || fc.rules?.some?.((r: any) => r.method === "required");

        // The field editor's control dropdown only lists KNOWN_FIELD_CONTROLS; a
        // persisted control it doesn't expose (e.g. the CodeControl we inject for
        // array/object) falls back to the default so the select stays valid. The
        // outputType is preserved, so a re-save re-derives the right control.
        const rawControl = fc.fieldControl ?? "InputControl";
        const fieldControl = KNOWN_FIELD_CONTROLS.has(rawControl) ? rawControl : "InputControl";

        return {
            key: field.key ?? "",
            label: wp.label ?? field.label ?? field.key ?? "",
            outputType: field.outputType ?? field.type ?? "string",
            fieldControl,
            required: !!hasRequired,
            placeholder: cp.placeholder ?? "",
            description: field.description ?? "",
            options,
            ...(serverOptions ? { serverOptions } : {}),
        };
    });
}

/* ================================================================== */
/*  Locked defaults  (ILockedDefaultEntry[] ↔ Record<string, any>)     */
/* ================================================================== */

export interface ILockedDefaultEntry {
    key: string;
    label: string;
    value: string;
    type: "string" | "number" | "boolean" | "json";
    locked: boolean;
    /** The worker field's `fieldControl` (e.g. "CodeControl"), so the lock row
     *  can render the same editor the worker uses instead of a generic input. */
    controlType?: string;
    /** The worker field's `controlProps` (e.g. `language`, `languageField`,
     *  `placeholder`) — needed to configure controls like the code editor. */
    controlProps?: Record<string, any>;
    options?: { id: string; value: string }[];
}

function outputTypeToEntryType(outputType: string): ILockedDefaultEntry["type"] {
    switch (outputType) {
        case "number": return "number";
        case "boolean": return "boolean";
        case "object":
        case "array": return "json";
        default: return "string";
    }
}

function extractOptions(schema: INodeInputSchema): { id: string; value: string }[] | undefined {
    const opts = schema.fieldConfig?.controlProps?.options;
    if (!Array.isArray(opts) || opts.length === 0) return undefined;
    return opts.map((o: any) => ({
        id: String(o.id ?? o.value ?? o),
        value: String(o.value ?? o.id ?? o),
    }));
}

/** Build lock entries from a worker's own input schema, prefilling existing defaults. */
export function workerSchemaToEntries(
    schema: INodeInputSchema[],
    existingDefaults?: Record<string, any>
): ILockedDefaultEntry[] {
    return schema.map((field) => {
        const hasExisting = existingDefaults != null && field.key in existingDefaults;
        const existingValue = hasExisting ? existingDefaults![field.key] : undefined;
        const entryType = outputTypeToEntryType(field.outputType);

        let strValue = "";
        if (hasExisting) {
            if (typeof existingValue === "string") strValue = existingValue;
            else if (typeof existingValue === "boolean" || typeof existingValue === "number") strValue = String(existingValue);
            else strValue = JSON.stringify(existingValue);
        }

        return {
            key: field.key,
            label: (field.fieldConfig as any)?.wrapperProps?.label || (field.fieldConfig as any)?.label || field.key,
            value: strValue,
            type: entryType,
            locked: hasExisting,
            controlType: field.fieldConfig?.fieldControl,
            controlProps: (field.fieldConfig as any)?.controlProps,
            options: extractOptions(field),
        };
    });
}

export function lockedDefaultsToEntries(
    obj: Record<string, any> | undefined | null,
    schema?: INodeInputSchema[]
): ILockedDefaultEntry[] {
    if (schema?.length) {
        return workerSchemaToEntries(schema, obj ?? undefined);
    }

    if (!obj || typeof obj !== "object") return [];
    return Object.entries(obj).map(([key, value]) => {
        let type: ILockedDefaultEntry["type"] = "string";
        let strValue = "";

        if (typeof value === "boolean") { type = "boolean"; strValue = String(value); }
        else if (typeof value === "number") { type = "number"; strValue = String(value); }
        else if (typeof value === "string") { type = "string"; strValue = value; }
        else { type = "json"; strValue = JSON.stringify(value); }

        return { key, label: key, value: strValue, type, locked: true };
    });
}

export function entriesToLockedDefaults(entries: ILockedDefaultEntry[]): Record<string, any> {
    const result: Record<string, any> = {};
    for (const entry of entries) {
        if (!entry.key || !entry.locked) continue;
        if (entry.value === "") continue;
        switch (entry.type) {
            case "number":
                result[entry.key] = Number(entry.value) || 0;
                break;
            case "boolean":
                result[entry.key] = entry.value === "true";
                break;
            case "json":
                try { result[entry.key] = JSON.parse(entry.value); }
                catch { result[entry.key] = entry.value; }
                break;
            default:
                result[entry.key] = entry.value;
        }
    }
    return result;
}

/* ================================================================== */
/*  Input mapping  (IMappingEntry[] ↔ Record<string, any>)             */
/* ================================================================== */

export interface IMappingEntry {
    workerField: string;
    /** For "expression" mode — the expression string */
    expressionValue: string;
    /** For "key_value_array" mode — array of name/value pairs */
    arrayItems: { name: string; value: string }[];
    /** For "json_template" mode — the raw JSON template string */
    jsonTemplate: string;
}

export function createMappingEntry(workerField: string, meta?: IWorkerFieldMeta): IMappingEntry {
    return {
        workerField,
        expressionValue: "",
        arrayItems: meta?.mode === "key_value_array" ? [{ name: "", value: "" }] : [],
        jsonTemplate: "",
    };
}

/** Resolve a worker field's metadata. When the caller passes the already-resolved
 *  `workerFields` (schema-aware, see resolveWorkerFields) use that; otherwise fall
 *  back to the static-registry lookup keyed by workerType. */
function metaFor(
    key: string,
    workerType?: string,
    workerFields?: IWorkerFieldMeta[],
): IWorkerFieldMeta | undefined {
    if (workerFields) return workerFields.find((f) => f.key === key);
    return getWorkerFieldMeta(key, workerType);
}

export function mappingEntriesToObject(
    entries: IMappingEntry[],
    workerType?: string,
    workerFields?: IWorkerFieldMeta[],
): Record<string, any> {
    const result: Record<string, any> = {};
    for (const entry of entries) {
        if (!entry.workerField) continue;
        const meta = metaFor(entry.workerField, workerType, workerFields);
        const mode = meta?.mode ?? "expression";

        if (mode === "key_value_array" && meta) {
            const items = entry.arrayItems
                .filter((item) => item.name || item.value)
                .map((item) => ({ [meta.nameKey!]: item.name, [meta.valueKey!]: item.value }));
            if (items.length > 0) result[entry.workerField] = items;
        } else if (mode === "json_template") {
            if (entry.jsonTemplate.trim()) result[entry.workerField] = entry.jsonTemplate;
        } else {
            if (entry.expressionValue.trim()) result[entry.workerField] = entry.expressionValue;
        }
    }
    return result;
}

export function objectToMappingEntries(
    obj: Record<string, any> | undefined | null,
    workerType?: string,
    workerFields?: IWorkerFieldMeta[],
): IMappingEntry[] {
    if (!obj || typeof obj !== "object") return [];
    return Object.entries(obj)
        .filter(([key]) => metaFor(key, workerType, workerFields) !== undefined)
        .map(([key, value]) => {
            const meta = metaFor(key, workerType, workerFields);
            const mode = meta?.mode ?? "expression";

            const entry: IMappingEntry = { workerField: key, expressionValue: "", arrayItems: [], jsonTemplate: "" };

            if (mode === "key_value_array" && Array.isArray(value) && meta) {
                entry.arrayItems = value.map((item: any) => ({
                    name: String(item[meta.nameKey!] ?? ""),
                    value: String(item[meta.valueKey!] ?? ""),
                }));
            } else if (mode === "json_template") {
                entry.jsonTemplate = typeof value === "string" ? value : JSON.stringify(value, null, 2);
            } else {
                entry.expressionValue = typeof value === "string" ? value : JSON.stringify(value);
            }

            return entry;
        });
}

/* ================================================================== */
/*  Output mapping  (IKeyValuePair[] ↔ Record<string, any>)            */
/* ================================================================== */

export interface IKeyValuePair {
    key: string;
    value: string;
}

export interface ISuggestion {
    value: string;
    label: string;
    description?: string;
}

export function keyValuePairsToObject(pairs: IKeyValuePair[]): Record<string, any> {
    const result: Record<string, any> = {};
    for (const pair of pairs) {
        if (pair.key) result[pair.key] = pair.value;
    }
    return result;
}

export function objectToKeyValuePairs(obj: Record<string, any> | undefined | null): IKeyValuePair[] {
    if (!obj || typeof obj !== "object") return [];
    return Object.entries(obj).map(([key, value]) => ({
        key,
        value: typeof value === "string" ? value : JSON.stringify(value),
    }));
}
