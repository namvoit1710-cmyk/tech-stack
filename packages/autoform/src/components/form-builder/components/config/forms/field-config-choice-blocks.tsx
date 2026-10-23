import type React from "react";
import type { UseFormRegister } from "react-hook-form";

import { Input } from "@ldc/ui/components/input";

import { CONTROLS_WITH_OPTIONS, resolveControlPropSchema } from "../../../constants";
import type { OutputType } from "../../../../../types/schema";
import type { DesignerNode } from "../../../types";
import { findNodeById } from "../../../utils/nodes";
import { ControlPropsSection } from "../sections/control-props-section";
import type { PropSchema } from "../../../constants/prop-keys";
import { outputTypeForControlProps } from "../sections/choice-output-type";
import { PropSchemaFields } from "../sections/prop-schema-fields";
import type { PropsGridProps } from "../sections/props-grid";
import { PropsGrid } from "../sections/props-grid";
import { TypedDefaultField } from "../sections/typed-default-field";
import { Field, Section } from "../shared/panel-primitives";
import type { ConfigFormValues } from "../shared/prop-value-utils";

const CHOICE_BOOLEAN = new Set(["CheckBoxControl", "SwitchControl"]);
/** Date/Time controls route their Default value through TypedDefaultField (a picker), not the raw Input (SA-2038 R3). */
const DATE_TIME_CONTROLS = new Set(["DatePickerControl", "DateTimePickerControl", "TimePickerControl"]);

/**
 * Two node-derived FieldConfigForm values, pulled out to keep that file under the config-folder
 * 300-line guard (`config-panel.size.test.ts`): whether the control is an ACTION component
 * (ButtonControl fires onFormActions — no data value, so the panel suppresses Default value +
 * Output type, SA-2040), and the selected node's sibling FIELD keys for every `field-ref` widget.
 * `findNodeById().list` is the node's own containing array, so its field members (minus self) are
 * exactly its siblings.
 */
export function deriveActionAndSiblings(
    node: DesignerNode,
    nodes: DesignerNode[],
    isContainer: boolean,
): {
    isActionComponent: boolean;
    siblingFieldKeys: string[];
    siblingFields: { key: string; outputType: OutputType }[];
} {
    const isActionComponent = !isContainer && node.fieldConfig?.fieldControl === "ButtonControl";
    const siblingFields = (findNodeById(nodes, node._id)?.list ?? [])
        .filter((n) => n._kind === "field" && n._id !== node._id && !!n.key)
        .map((n) => ({
            key: n.key as string,
            outputType: ((n as { outputType?: OutputType }).outputType ?? "string") as OutputType,
        }));
    const siblingFieldKeys = siblingFields.map((f) => f.key);
    return { isActionComponent, siblingFieldKeys, siblingFields };
}

/**
 * Derived Choice-control state + the two writes that couple `controlProps` to `outputType`
 * and set a typed default (SA-2036 Task 15). Pulled out of `FieldConfigForm` to keep that
 * file under the config-folder 300-line guard (`config-panel.size.test.ts`, AC 8) — a plain
 * factory (not a hook), mirroring `createFieldConfigActions` in this same directory.
 */
export function createChoiceControlWiring(
    node: DesignerNode,
    updateNode: (id: string, fn: (n: DesignerNode) => DesignerNode) => void,
    isContainer: boolean,
    /**
     * Keeps the config-form's RHF `"outputType"` field in sync when `flushChoiceControlProps`
     * writes a new node `outputType` out-of-band (via `updateNode`). Without this, the Output
     * type `<Select>` — which reads `watch("outputType")` — shows a stale value until the node
     * is re-selected, because the panel's re-sync effect only fires on `[node._id]`, not on
     * every `outputType` change (SA-2036 live-verify finding).
     */
    syncOutputType?: (outputType: string) => void,
) {
    const controlKey = node.fieldConfig?.fieldControl ?? "";
    const isChoiceControl = !isContainer && (CHOICE_BOOLEAN.has(controlKey) || CONTROLS_WITH_OPTIONS.has(controlKey));
    const isOptionSource = !isContainer && CONTROLS_WITH_OPTIONS.has(controlKey);
    const controlPropsObj: Record<string, unknown> = node.fieldConfig?.controlProps ?? {};
    const choiceScalarSchema = resolveControlPropSchema(controlKey);

    /** Whole-object controlProps write for Choice controls, coupling outputType to `multiple`. */
    const flushChoiceControlProps = (next: Record<string, unknown>) => {
        const nextOutputType = outputTypeForControlProps(controlKey, next, node.outputType);
        syncOutputType?.(nextOutputType);
        updateNode(node._id, (n) => ({
            ...n,
            outputType: outputTypeForControlProps(controlKey, next, n.outputType) as typeof n.outputType,
            fieldConfig: { ...n.fieldConfig, controlProps: next },
        }));
    };

    const setDefaultTyped = (v: unknown) =>
        updateNode(node._id, (n) => (n._kind === "field" ? { ...n, default: v } : n));

    return {
        controlKey,
        isChoiceControl,
        isOptionSource,
        controlPropsObj,
        choiceScalarSchema,
        flushChoiceControlProps,
        setDefaultTyped,
    };
}

export interface DefaultValueFieldProps {
    isContainer: boolean;
    isChoiceControl: boolean;
    controlKey: string;
    controlPropsObj: Record<string, unknown>;
    nodeDefault: unknown;
    onChangeTyped: (next: unknown) => void;
    register: UseFormRegister<ConfigFormValues>;
    setDefault: (v: unknown) => void;
}

/**
 * The Identity "Default value" field: typed (`TypedDefaultField`) for Choice controls,
 * else the SA-2014 Number/string `<Input>` pair — unchanged, just relocated.
 */
export function DefaultValueField({
    isContainer,
    isChoiceControl,
    controlKey,
    controlPropsObj,
    nodeDefault,
    onChangeTyped,
    register,
    setDefault,
}: DefaultValueFieldProps) {
    if (isContainer) return null;
    return (
        <Field label="Default value">
            {isChoiceControl || DATE_TIME_CONTROLS.has(controlKey) ? (
                <TypedDefaultField
                    controlKey={controlKey}
                    controlProps={controlPropsObj}
                    value={nodeDefault}
                    onChange={onChangeTyped}
                />
            ) : controlKey === "NumberControl" ? (
                <Input
                    type="number"
                    {...register("default", {
                        onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
                            const raw = e.target.value;
                            // Clear = no default (delete); otherwise a real number.
                            setDefault(raw.trim() === "" ? undefined : Number(raw));
                        },
                    })}
                    className="h-8 text-xs!"
                    placeholder="default value"
                />
            ) : (
                <Input
                    {...register("default", {
                        onChange: (e: React.ChangeEvent<HTMLInputElement>) => setDefault(e.target.value),
                    })}
                    className="h-8 text-xs!"
                    placeholder="default value"
                />
            )}
        </Field>
    );
}

export interface ControlPropsBlockProps {
    isChoiceControl: boolean;
    choiceScalarSchema: PropSchema[];
    controlPropsObj: Record<string, unknown>;
    flushChoiceControlProps: (next: Record<string, unknown>) => void;
    controlSchema: PropSchema[];
    nodeId: string;
    controlProps: Record<string, unknown>;
    flushControlPropsObject: (next: Record<string, unknown>) => void;
    /** Sibling field keys threaded to the typed section's `field-ref` widgets (SA-2040). */
    fieldRefOptions?: string[];
    /** Everything the SA-2014 raw grid fallback needs, minus the fixed `variant`/`name`. */
    rawGrid: Omit<PropsGridProps, "variant" | "name">;
}

/**
 * Control Props — Choice controls first (typed scalars, couples outputType), then the
 * SA-2014 typed-schema section, else the raw key/value grid. Unchanged logic, relocated.
 */
export function ControlPropsBlock({
    isChoiceControl,
    choiceScalarSchema,
    controlPropsObj,
    flushChoiceControlProps,
    controlSchema,
    nodeId,
    controlProps,
    flushControlPropsObject,
    fieldRefOptions,
    rawGrid,
}: ControlPropsBlockProps) {
    if (isChoiceControl) {
        return (
            <>
                <Section title="Control" />
                <PropSchemaFields schema={choiceScalarSchema} value={controlPropsObj} onChange={flushChoiceControlProps} />
            </>
        );
    }
    if (controlSchema.length > 0) {
        // SA-2038 Task 4: route through the COUPLED flush (not flushControlPropsObject) so
        // Date/DateTime `mode=range` couples outputType=object and the Output-type Select
        // stays in sync. Behavior-preserving for every other schema control (Input/Textarea/
        // Password/Number/Time): outputTypeForControlProps returns `fallback` for them, so
        // outputType is left untouched.
        return (
            <ControlPropsSection
                nodeId={nodeId}
                schema={controlSchema}
                value={controlProps}
                onChange={flushChoiceControlProps}
                fieldRefOptions={fieldRefOptions}
            />
        );
    }
    return <PropsGrid variant="control" name="controlProps" {...rawGrid} />;
}
