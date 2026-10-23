import type { FieldArrayWithId, UseFieldArrayAppend, UseFieldArrayRemove, UseFormReturn } from "react-hook-form";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

import { ODATA_SLOT_NAMES, OUTPUT_TYPE_OPTIONS } from "../../../constants";
import type { PropSchema } from "../../../constants/prop-keys";
import type { DesignerContainer } from "../../../types";
import { ControlPropsBlock } from "../forms/field-config-choice-blocks";
import { CollapsibleSection } from "../shared/collapsible-section";
import { Field } from "../shared/panel-primitives";
import type { ConfigFormValues, WrapperPropEntry } from "../shared/prop-value-utils";
import { PropsGrid } from "./props-grid";

/**
 * The "More settings" group (collapsed by default). Holds the four blocks lifted out of
 * `FieldConfigForm` — Output type, Slot, Wrapper Props and Control Props — so the parent
 * stays under the config-folder 300-line guard (`config-panel.size.test.ts`). The trigger is
 * deliberately NOT labelled "Advanced": the Control Props typed panel already owns an
 * "Advanced (raw props)" escape-hatch button, and a second `/advanced/i` button breaks the
 * SA-2014 `getByRole` queries. Every moved
 * block's JSX, guards and handler wiring are byte-identical to the parent's originals; only
 * their physical location changed.
 *
 * `CollapsibleSection`'s `keepMounted` keeps this content in the DOM (CSS-hidden) while
 * closed rather than unmounting it: the characterization test asserts the Control Props
 * inputs are IN THE DOM at initial render even though More settings starts collapsed.
 */
export interface AdvancedConfigSectionProps {
    form: UseFormReturn<ConfigFormValues>;
    isActionComponent: boolean;
    isContainer: boolean;
    parent: DesignerContainer | null;
    setOutputType: (v: string) => void;
    setSlot: (v: string) => void;
    // Wrapper Props grid
    wpFields: FieldArrayWithId<ConfigFormValues, "wrapperProps", "id">[];
    wpAppend: UseFieldArrayAppend<ConfigFormValues, "wrapperProps">;
    wpRemove: UseFieldArrayRemove;
    wrapperKeySuggestions: string[];
    flushWrapperProps: (entries: WrapperPropEntry[]) => void;
    // Control Props block
    cpFields: FieldArrayWithId<ConfigFormValues, "controlProps", "id">[];
    cpAppend: UseFieldArrayAppend<ConfigFormValues, "controlProps">;
    cpRemove: UseFieldArrayRemove;
    controlKeySuggestions: string[];
    flushControlProps: (entries: WrapperPropEntry[]) => void;
    siblingFieldKeys: string[];
    isChoiceControl: boolean;
    choiceScalarSchema: PropSchema[];
    controlPropsObj: Record<string, unknown>;
    flushChoiceControlProps: (next: Record<string, unknown>) => void;
    controlSchema: PropSchema[];
    nodeId: string;
    controlProps: Record<string, unknown>;
    flushControlPropsObject: (next: Record<string, unknown>) => void;
}

export function AdvancedConfigSection({
    form,
    isActionComponent,
    isContainer,
    parent,
    setOutputType,
    setSlot,
    wpFields,
    wpAppend,
    wpRemove,
    wrapperKeySuggestions,
    flushWrapperProps,
    cpFields,
    cpAppend,
    cpRemove,
    controlKeySuggestions,
    flushControlProps,
    siblingFieldKeys,
    isChoiceControl,
    choiceScalarSchema,
    controlPropsObj,
    flushChoiceControlProps,
    controlSchema,
    nodeId,
    controlProps,
    flushControlPropsObject,
}: AdvancedConfigSectionProps) {
    const { setValue, getValues, watch } = form;

    return (
        <CollapsibleSection label="More settings" keepMounted>
            <div className="pt-2">
                {!isActionComponent && (
                    <Field label="Output type">
                        <Select
                            value={watch("outputType")}
                            onValueChange={(v) => {
                                setValue("outputType", v);
                                setOutputType(v);
                            }}
                        >
                            <SelectTrigger size="sm" className="text-xs! w-full">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {OUTPUT_TYPE_OPTIONS.map((o) => (
                                    <SelectItem key={o.value} value={o.value} className="text-xs!">
                                        {o.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </Field>
                )}

                {!isContainer && parent?.wrapperType === "OdataWrapper" && (
                    <Field label="Slot">
                        <Select
                            value={watch("slot")}
                            onValueChange={(v) => {
                                setValue("slot", v);
                                setSlot(v);
                            }}
                        >
                            <SelectTrigger size="sm" className="text-xs! w-full">
                                <SelectValue placeholder="none" />
                            </SelectTrigger>
                            <SelectContent>
                                {ODATA_SLOT_NAMES.map((s) => (
                                    <SelectItem key={s} value={s} className="text-xs!">
                                        {s}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </Field>
                )}

                {!isContainer && (
                    <>
                        <div className="grid grid-cols-1 gap-2 px-3 mb-2">
                            <PropsGrid
                                variant="wrapper"
                                form={form}
                                name="wrapperProps"
                                fields={wpFields}
                                suggestions={wrapperKeySuggestions}
                                onAdd={() => wpAppend({ key: "", value: "", type: "string" })}
                                onRemove={(i) => {
                                    wpRemove(i);
                                    flushWrapperProps(getValues("wrapperProps").filter((_, idx) => idx !== i));
                                }}
                                onFlush={() => flushWrapperProps(getValues("wrapperProps"))}
                            />
                        </div>

                        {/* Control Props — Choice controls first (typed scalars, couples outputType),
                            then the SA-2014 typed-schema section, else the raw key/value grid */}
                        <ControlPropsBlock
                            fieldRefOptions={siblingFieldKeys}
                            isChoiceControl={isChoiceControl}
                            choiceScalarSchema={choiceScalarSchema}
                            controlPropsObj={controlPropsObj}
                            flushChoiceControlProps={flushChoiceControlProps}
                            controlSchema={controlSchema}
                            nodeId={nodeId}
                            controlProps={controlProps}
                            flushControlPropsObject={flushControlPropsObject}
                            rawGrid={{
                                form,
                                fields: cpFields,
                                suggestions: controlKeySuggestions,
                                onAdd: () => cpAppend({ key: "", value: "", type: "string" }),
                                onRemove: (i) => {
                                    cpRemove(i);
                                    flushControlProps(getValues("controlProps").filter((_, idx) => idx !== i));
                                },
                                onFlush: () => flushControlProps(getValues("controlProps")),
                            }}
                        />
                    </>
                )}
            </div>
        </CollapsibleSection>
    );
}

export default AdvancedConfigSection;
