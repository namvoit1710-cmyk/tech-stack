import type { PropSchema } from "../../../constants/prop-keys";
import { CollapsibleSection } from "../shared/collapsible-section";
import { Section } from "../shared/panel-primitives";
import { PropSchemaFields } from "./prop-schema-fields";
import { RawPropsEditor } from "./raw-props-editor";

export interface ControlPropsSectionProps {
    /** The selected node's id — changing it re-seeds the raw editor's rows. */
    nodeId: string;
    schema: PropSchema[];
    /** The node's current controlProps. */
    value: Record<string, unknown>;
    /** Receives the whole next controlProps object; the form flushes it to the node. */
    onChange: (next: Record<string, unknown>) => void;
    /** Sibling field keys for any `field-ref` widget in the schema (SA-2040). */
    fieldRefOptions?: string[];
}

/**
 * The typed Control Props block for a field control that has a `CONTROL_PROP_SCHEMA`.
 *
 *   Control Props — the schema's props, rendered by the generic `PropSchemaFields`.
 *   Advanced      — `RawPropsEditor` over everything the schema does NOT cover, so an unknown or
 *                   legacy prop stays visible, editable and SAVED. Dropping a prop from the
 *                   schema removes it from the curated UI, never from the document.
 *
 * Both write through the SAME `onChange` (the whole controlProps object): `PropSchemaFields`
 * spreads the props it did not touch and `RawPropsEditor` copies covered keys through first, so
 * neither half drops the other's keys. `options` is treated as covered — it is owned by the
 * dedicated Options section and must never be edited as a raw row.
 */
export function ControlPropsSection({ nodeId, schema, value, onChange, fieldRefOptions }: ControlPropsSectionProps) {
    const coveredKeys = [...schema.map((p) => p.key), "options"];
    const hiddenCount = Object.keys(value).filter((k) => !coveredKeys.includes(k)).length;

    return (
        <>
            <Section title="Control Props" />
            <PropSchemaFields schema={schema} value={value} onChange={onChange} fieldRefOptions={fieldRefOptions} />

            <CollapsibleSection
                label="Advanced (raw props)"
                count={hiddenCount > 0 ? hiddenCount : undefined}
                countTestId="advanced-hidden-count"
            >
                <RawPropsEditor
                    nodeId={nodeId}
                    value={value}
                    coveredKeys={coveredKeys}
                    suggestions={[]}
                    onChange={onChange}
                />
            </CollapsibleSection>
        </>
    );
}

export default ControlPropsSection;
