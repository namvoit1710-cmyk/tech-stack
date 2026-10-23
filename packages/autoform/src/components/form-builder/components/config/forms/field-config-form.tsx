import { useEffect } from "react";
import { useFieldArray, useForm } from "react-hook-form";

import { Input } from "@ldc/ui/components/input";

import type { ISelectOption } from "../../../../field-control/select-control";
import {
    CONTROLS_WITH_OPTIONS,
    resolveContainerWrapperPropKeys,
    resolveControlPropKeys,
    resolveControlPropSchema,
    resolveFieldWrapperPropKeys,
} from "../../../constants";
import { useDesignerCtx } from "../../../hooks/use-designer-context";
import type { DesignerNode } from "../../../types";
import { findParentContainer } from "../../../utils/nodes";
import { AdvancedConfigSection } from "../sections/advanced-config-section";
import { ChoiceConfigSection } from "../sections/choice-config-section";
import { ConditionEditor } from "../sections/condition-editor";
import { EventsEditor } from "../sections/events-editor";
import { OptionsEditor } from "../sections/options-editor";
import { RulesEditor } from "../sections/rules-editor";
import { WrapperSegment } from "../sections/wrapper-segment";
import { CollapsibleSection } from "../shared/collapsible-section";
import { LivePreviewCard } from "../shared/live-preview-card";
import { Field } from "../shared/panel-primitives";
import type { ConfigFormValues, RuleEntry } from "../shared/prop-value-utils";
import { stringifyValue, toControlPropEntries, toWpEntries } from "../shared/prop-value-utils";
import { createChoiceControlWiring, DefaultValueField, deriveActionAndSiblings } from "./field-config-choice-blocks";
import { createFieldConfigActions } from "./field-config-actions";

/**
 * The node's rules as field-array entries. Derived on EVERY render (not held in this form):
 * `RulesEditor` owns the field array now, and re-seeds itself from this prop when `nodeId`
 * changes — exactly what the panel's `useEffect` on `node._id` did when it called
 * `ruleReplace(...)` with this same mapping.
 */
function toRuleEntries(node: DesignerNode): RuleEntry[] {
    return (node.fieldConfig?.rules ?? []).map((r) => ({
        method: r.method,
        value: r.value === undefined || r.value === null ? "" : String(r.value),
        message: r.message ?? "",
    }));
}

/**
 * The generic config form: Identity · Wrapper/Control props · Options · Validation · Condition.
 *
 * It is only ever rendered for a FIELD or a PLAIN container (`DivWrapper`, `FragmentWrapper`,
 * `FormItemWrapper`). The typed containers (`OdataWrapper`, `TabWrapper`) are dispatched away
 * by `ConfigPanel` BEFORE this component mounts — which is what lets every hook below sit
 * unconditionally at the top. This form used to OPEN with those two container early-returns,
 * ahead of `useDesignerCtx` / `useForm` / `useFieldArray` / `useEffect`: a rules-of-hooks
 * violation that only survived because the container branch happened to call zero stateful
 * hooks. The dispatcher retires it structurally.
 */
export function FieldConfigForm({
    node,
    updateNode,
}: {
    node: DesignerNode;
    updateNode: (id: string, fn: (n: DesignerNode) => DesignerNode) => void;
}) {
    const { nodes } = useDesignerCtx();

    const isContainer = node._kind === "container";
    const parent = findParentContainer(nodes, node._id);
    const controlKeySuggestions = isContainer ? [] : resolveControlPropKeys(node.fieldConfig?.fieldControl);
    const controlSchema = isContainer ? [] : resolveControlPropSchema(node.fieldConfig?.fieldControl);
    const wrapperKeySuggestions = isContainer
        ? resolveContainerWrapperPropKeys(node.wrapperType)
        : resolveFieldWrapperPropKeys(parent?.wrapperType ?? null);
    const fc = node.fieldConfig;
    // ButtonControl → action component (suppress Default value + Output type); sibling field keys
    // feed every `field-ref` widget. Derived in field-config-choice-blocks to keep this file lean.
    const { isActionComponent, siblingFieldKeys, siblingFields } = deriveActionAndSiblings(node, nodes, isContainer);
    const opts: ISelectOption[] = fc.controlProps?.options ?? [];
    const showOpts = !isContainer && CONTROLS_WITH_OPTIONS.has(node.fieldConfig.fieldControl ?? "");

    const form = useForm<ConfigFormValues>({
        defaultValues: {
            key: node.key ?? "",
            outputType: node.outputType ?? "string",
            fieldWrapper: fc.fieldWrapper ?? "FormItemWrapper",
            wrapperProps: toWpEntries(fc.wrapperProps),
            controlProps: toControlPropEntries(fc.controlProps),
            default: stringifyValue((node as { default?: unknown }).default ?? ""),
            slot: node.fieldConfig?.slot ?? "",
        },
    });
    const { register, control, setValue, watch } = form;

    const { fields: wpFields, append: wpAppend, remove: wpRemove, replace: wpReplace } = useFieldArray({
        control,
        name: "wrapperProps",
    });

    const { fields: cpFields, append: cpAppend, remove: cpRemove, replace: cpReplace } = useFieldArray({
        control,
        name: "controlProps",
    });

    // Re-sync when a different node is selected. `rules` and the condition are no longer
    // replayed here — `RulesEditor` re-seeds itself from `nodeId`, and `ConditionEditor`
    // reads the node directly, so both are always in sync with the selected node by
    // construction.
    useEffect(() => {
        setValue("key", node.key ?? "");
        setValue("outputType", node.outputType ?? "string");
        setValue("fieldWrapper", fc.fieldWrapper ?? "FormItemWrapper");
        wpReplace(toWpEntries(fc.wrapperProps));
        cpReplace(toControlPropEntries(fc.controlProps));
        setValue("default", stringifyValue((node as { default?: unknown }).default ?? ""));
        setValue("slot", node.fieldConfig?.slot ?? "");
    }, [node._id]);

    const {
        setKey,
        setLabel,
        setFieldWrapper,
        setOutputType,
        setDefault,
        setSlot,
        flushWrapperProps,
        flushControlProps,
        flushControlPropsObject,
        flushRules,
        flushOptions,
        setCondition,
        setScript,
    } = createFieldConfigActions(node, updateNode);

    // `label` is the node's canvas display name (bound to `wrapperProps.label`) — moved to
    // Basics from Wrapper Props' old pinned first row (SA-2156). A hand-authored document
    // could hold a non-string here; only a string/number has a sane single-line rendering.
    const labelValue =
        typeof fc.wrapperProps?.label === "string" || typeof fc.wrapperProps?.label === "number"
            ? String(fc.wrapperProps.label)
            : "";

    const {
        controlKey,
        isChoiceControl,
        isOptionSource,
        controlPropsObj,
        choiceScalarSchema,
        flushChoiceControlProps,
        setDefaultTyped,
    } = createChoiceControlWiring(node, updateNode, isContainer, (t) => setValue("outputType", t));

    return (
        <div className="py-2 space-y-0">
            {/* Live preview (new, top) — renders the real control from the node's live config */}
            <CollapsibleSection label="Live preview" defaultOpen>
                <div className="px-4">
                    <LivePreviewCard node={node} />
                </div>
            </CollapsibleSection>

            {/* Basics: Label + Key + Default value + Wrapper (segmented) — closed by default (SA-2175), only Live preview opens */}
            <CollapsibleSection label="Basics">
                <Field label="Label">
                    <Input
                        aria-label="Label"
                        placeholder="Text Input"
                        className="h-8 text-xs!"
                        value={labelValue}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLabel(e.target.value)}
                    />
                </Field>

                <Field label="Key">
                    <Input
                        {...register("key", { onChange: (e: React.ChangeEvent<HTMLInputElement>) => setKey(e.target.value) })}
                        className="h-8 text-xs!"
                        placeholder="e.g. email"
                    />
                </Field>

                {!isActionComponent && (
                    <DefaultValueField
                        isContainer={isContainer}
                        isChoiceControl={isChoiceControl}
                        controlKey={controlKey}
                        controlPropsObj={controlPropsObj}
                        nodeDefault={(node as { default?: unknown }).default}
                        onChangeTyped={setDefaultTyped}
                        register={register}
                        setDefault={setDefault}
                    />
                )}

                {!isContainer && (
                    <WrapperSegment
                        value={watch("fieldWrapper")}
                        onChange={(v) => {
                            setValue("fieldWrapper", v);
                            setFieldWrapper(v);
                        }}
                    />
                )}
            </CollapsibleSection>

            {/* More settings — Output type · Slot · Wrapper Props · Control Props (collapsed) */}
            <AdvancedConfigSection
                form={form}
                isActionComponent={isActionComponent}
                isContainer={isContainer}
                parent={parent}
                setOutputType={setOutputType}
                setSlot={setSlot}
                wpFields={wpFields}
                wpAppend={wpAppend}
                wpRemove={wpRemove}
                wrapperKeySuggestions={wrapperKeySuggestions}
                flushWrapperProps={flushWrapperProps}
                cpFields={cpFields}
                cpAppend={cpAppend}
                cpRemove={cpRemove}
                controlKeySuggestions={controlKeySuggestions}
                flushControlProps={flushControlProps}
                siblingFieldKeys={siblingFieldKeys}
                isChoiceControl={isChoiceControl}
                choiceScalarSchema={choiceScalarSchema}
                controlPropsObj={controlPropsObj}
                flushChoiceControlProps={flushChoiceControlProps}
                controlSchema={controlSchema}
                nodeId={node._id}
                controlProps={fc.controlProps ?? {}}
                flushControlPropsObject={flushControlPropsObject}
            />

            {/* Options — Choice option-source controls route to the data-source selector */}
            {isOptionSource ? (
                <ChoiceConfigSection
                    controlKey={controlKey}
                    controlProps={controlPropsObj}
                    onControlPropsChange={flushChoiceControlProps}
                />
            ) : (
                showOpts && <OptionsEditor options={opts} onChange={flushOptions} />
            )}

            {/* Events */}
            <EventsEditor node={node} setScript={setScript} />

            {/* Validation */}
            <RulesEditor nodeId={node._id} rules={toRuleEntries(node)} onChange={flushRules} />

            {/* Condition */}
            <ConditionEditor condition={fc.condition ?? null} siblingFields={siblingFields} onChange={setCondition} />
        </div>
    );
}

export default FieldConfigForm;
