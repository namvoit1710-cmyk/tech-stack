import type { IValidationMethod } from "../../../../../types/validation";

/**
 * The four types a raw key/value prop row can declare. Moved verbatim out of
 * `config-panel.tsx` — every helper below is the panel's original implementation,
 * unchanged, so the extraction is behaviour-preserving.
 */
export type WpType = "string" | "number" | "boolean" | "json";

/** The four types, in the order every type-selector offers them. */
export const WP_TYPES: WpType[] = ["string", "number", "boolean", "json"];

export interface WrapperPropEntry {
    key: string;
    value: string;
    type: WpType;
}

export interface RuleEntry {
    method: IValidationMethod;
    value: string;
    message: string;
}

/**
 * The react-hook-form shape of the field config form (`forms/field-config-form.tsx`).
 *
 * It lives here rather than beside the form because `sections/props-grid.tsx` — which
 * renders the form's two field arrays and is imported BY the form — has to name the same
 * type. Declaring it in the form file would make the two import each other.
 */
export interface ConfigFormValues {
    key: string;
    outputType: string;
    default: string;
    slot: string;
    fieldWrapper: string;
    wrapperProps: WrapperPropEntry[];
    controlProps: WrapperPropEntry[];
}

/** Infer WpType from a raw JS value coming from the node. */
export function inferType(v: unknown): WpType {
    if (typeof v === "number") return "number";
    if (typeof v === "boolean") return "boolean";
    if (typeof v === "object" && v !== null) return "json";
    return "string";
}

/**
 * Stringify an unknown value for display in a text input (objects → COMPACT JSON).
 *
 * Deliberately NOT the pretty-printing `stringifyRowValue` in
 * `sections/raw-props-editor.tsx`: this one is what a single-line input shows
 * (`default`, `condValue`, the pinned `label` row), and the field-array seeders below
 * pretty-print objects themselves before they ever reach it. Merging the two would
 * change what those single-line inputs render.
 */
export function stringifyValue(v: unknown): string {
    if (v === null || v === undefined) return "";
    if (typeof v === "object") return JSON.stringify(v);
    if (typeof v === "string") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return JSON.stringify(v);
}

/** Coerce a string value back to a JS value based on its declared type. */
export function coerceValue(type: WpType, value: string): unknown {
    if (type === "number") return Number(value);
    if (type === "boolean") return value === "true";
    if (type === "json") {
        try { return JSON.parse(value); } catch { return value; }
    }
    return value;
}

/**
 * Convert a wrapperProps object → field-array entries. `label` is excluded — SA-2156 moved it
 * to Basics (a first-class field bound straight to `wrapperProps.label`), so this grid no
 * longer pins, or even offers, that row.
 */
export function toWpEntries(wp: Record<string, unknown> | undefined): WrapperPropEntry[] {
    return Object.entries(wp ?? {})
        .filter(([k]) => k !== "label")
        .map(([k, v]) => ({
            key: k,
            value: typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : stringifyValue(v),
            type: inferType(v),
        }));
}

/** Convert a controlProps object → field-array entries (no pinned rows). */
export function toControlPropEntries(cp: Record<string, unknown> | undefined): WrapperPropEntry[] {
    return Object.entries(cp ?? {})
        .filter(([k]) => k !== "options") // options managed by dedicated section
        .map(([k, v]) => ({
            key: k,
            value: typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : stringifyValue(v),
            type: inferType(v),
        }));
}
