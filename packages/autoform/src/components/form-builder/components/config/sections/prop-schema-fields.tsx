import type React from "react";
import { useId } from "react";

import type { PropSchema, PropType } from "../../../constants/prop-keys";
import { DateTimeValueWidget } from "./date-time-value-widget";
import { FieldRefWidget, IconWidget } from "./field-ref-icon-widgets";
import {
    BooleanWidget,
    EnumWidget,
    Help,
    JsonWidget,
    KeyValueWidget,
    ListWidget,
    OptionsWidget,
    ScalarWidget,
    type WidgetProps,
} from "./prop-widgets";

export type { WidgetProps } from "./prop-widgets";

/**
 * Nested `fields: PropSchema[]` group — recurses into `PropSchemaFields` itself, which is what
 * makes the schema tree recursive (`serverOptions`, `responseMapping`, `odataConfig`, …). Kept
 * in this file (rather than `prop-widgets.tsx`) because it closes the recursive loop back onto
 * `PropSchemaFields` below — moving it out would make the two files import-cycle on each other.
 */
function ObjectWidget({ prop, current, label, set }: WidgetProps) {
    const nested =
        current && typeof current === "object" && !Array.isArray(current)
            ? (current as Record<string, unknown>)
            : {};
    return (
        <div className="rounded-md border border-border/60 p-2 space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
            <PropSchemaFields schema={prop.fields ?? []} value={nested} onChange={(next) => set(prop.key, next)} />
            <Help text={prop.help} />
        </div>
    );
}

/**
 * `PropType` -> widget. `Partial` on purpose: a schema naming a `PropType` with no entry here
 * falls back to `ScalarWidget` rather than crashing. Adding a widget later is ONE entry here,
 * not a new branch.
 */
const WIDGETS: Partial<Record<PropType, React.FC<WidgetProps>>> = {
    boolean: BooleanWidget,
    enum: EnumWidget,
    json: JsonWidget,
    string: ScalarWidget,
    number: ScalarWidget,
    options: OptionsWidget,
    list: ListWidget,
    keyValue: KeyValueWidget,
    object: ObjectWidget,
    // SA-2038 — date/datetime/time share one widget that branches on prop.type internally.
    date: DateTimeValueWidget,
    datetime: DateTimeValueWidget,
    time: DateTimeValueWidget,
    // SA-2040 — Advanced controls: sibling field-key picker + lucide icon picker.
    "field-ref": FieldRefWidget,
    icon: IconWidget,
};

export interface PropSchemaFieldsProps {
    schema: PropSchema[];
    /** The current wrapperProps / controlProps object. */
    value: Record<string, unknown>;
    /** Receives the whole next object; the caller flushes it to the node. */
    onChange: (next: Record<string, unknown>) => void;
    /** Sibling field keys offered by every `field-ref` widget in this schema (SA-2040). */
    fieldRefOptions?: string[];
}

/**
 * Generic `PropType` -> widget renderer. Deliberately NOT OData-specific: tabs, tables, controls
 * and the date/time controls all reuse it unchanged.
 */
export function PropSchemaFields({ schema, value, onChange, fieldRefOptions }: PropSchemaFieldsProps) {
    // Unique per instance: wrapperProps and controlProps panels can render side by side with
    // overlapping keys (`readonly`), and duplicate ids break `htmlFor`.
    const uid = useId();

    const set = (key: string, next: unknown) => onChange({ ...value, [key]: next });
    const unset = (key: string) => {
        const next = { ...value };
        delete next[key];
        onChange(next);
    };

    return (
        <div className="mb-2 space-y-2 px-3">
            {schema.map((prop) => {
                const label = prop.label ?? prop.key;
                // `??`, never `||`: a stored `false` must still beat a `default: true`.
                const current = value[prop.key] ?? prop.default;
                const id = `${uid}-${prop.key}`;
                const Widget = WIDGETS[prop.type] ?? ScalarWidget;
                return (
                    <Widget
                        key={prop.key}
                        prop={prop}
                        current={current}
                        id={id}
                        label={label}
                        set={set}
                        unset={unset}
                        fieldRefOptions={fieldRefOptions}
                    />
                );
            })}
        </div>
    );
}

export default PropSchemaFields;
