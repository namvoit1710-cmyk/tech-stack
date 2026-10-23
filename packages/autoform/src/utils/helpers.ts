import type { IField } from "../types/schema";

export function getDefaultValues(
    fields: IField[] = []
): Record<string, unknown> {
    return fields.reduce<Record<string, unknown>>((acc, field) => {
        const { key, outputType, default: defaultVal, fields: children } = field;

        if (outputType === "object") {
            acc[key] = defaultVal ?? getDefaultValues(children ?? []);
        }
        else if (defaultVal !== undefined) {
            acc[key] = defaultVal;
        }

        return acc;
    }, {});
}

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
    typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * Coerces incoming form values to the shape the schema renders. The only
 * transform today is the inverse of the backend's `store_as="dict"` fold:
 * an array-table field (`fieldWrapper: "TableRowWrapper"`) whose value arrives
 * as a dict (e.g. WORKFLOW `input_mapping` = `{ crId: "{{$…}}" }`) is unfolded
 * into the `[{ <nameKey>, <valueKey> }]` rows the table form needs, using the
 * first two item-field keys (e.g. `param_name` / `param_value`). Without this,
 * `useFieldArray` receives a non-array and renders zero rows.
 *
 * Recurses through object fields. Returns a shallow clone — never mutates input.
 */
export function normalizeArrayTableValues(
    fields: IField[] = [],
    values: unknown
): unknown {
    if (!isPlainObject(values)) return values;

    const out: Record<string, unknown> = { ...values };

    for (const field of fields) {
        const value = out[field.key];
        if (value == null) continue;

        if (field.outputType === "object" && field.fields?.length) {
            out[field.key] = normalizeArrayTableValues(field.fields, value);
            continue;
        }

        const itemFields = field.fields?.[0]?.fields;
        const nameField = itemFields?.[0];
        const valueField = itemFields?.[1];
        if (
            field.outputType === "array"
            && field.fieldConfig?.fieldWrapper === "TableRowWrapper"
            && nameField
            && valueField
            && isPlainObject(value)
        ) {
            const { key: nameKey } = nameField;
            const { key: valueKey } = valueField;
            out[field.key] = Object.entries(value).map(([rowKey, rowValue]) => ({
                [nameKey]: rowKey,
                [valueKey]: rowValue,
            }));
        }
    }

    return out;
}