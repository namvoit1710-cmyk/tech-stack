import type { IConditionConfig } from "../../../../../types/condition";
import type { ISelectOption } from "../../../../field-control/select-control";
import type { DesignerNode } from "../../../types";
import { coerceRuleValue, ruleTakesValue } from "../../../utils/rules-utils";
import type { RuleEntry, WrapperPropEntry } from "../shared/prop-value-utils";
import { coerceValue } from "../shared/prop-value-utils";

/**
 * Every write `FieldConfigForm` makes to the selected node, in one place.
 *
 * A plain factory, not a hook: these were inline closures over `node` / `updateNode` in
 * `config-panel.tsx` and they stay exactly that — rebuilt on each render, holding no state.
 * Lifting them out is a pure move, so the flush semantics are unchanged, including the two
 * that are easy to get wrong:
 *
 * - `flushWrapperProps` / `flushControlProps` DROP entries whose key is blank, so a freshly
 *   appended (still empty) row never reaches the document. This is why `Add` alone writes
 *   nothing and the first keystroke is what writes.
 * - `flushControlProps` re-attaches `options` from the node afterwards: `OptionsEditor` owns
 *   that key, and the field array never carries it (`toControlPropEntries` filters it out).
 */
export function createFieldConfigActions(
    node: DesignerNode,
    updateNode: (id: string, fn: (n: DesignerNode) => DesignerNode) => void,
) {
    const patch = (updater: (n: DesignerNode) => DesignerNode) => updateNode(node._id, updater);

    /** Reduce field-array entries to a props object, coercing each value by its declared type. */
    const toProps = (entries: WrapperPropEntry[]) =>
        entries.reduce<Record<string, unknown>>((acc, { key, value, type }) => {
            if (key.trim()) acc[key.trim()] = coerceValue(type, value);
            return acc;
        }, {});

    return {
        setKey: (v: string) => patch((n) => ({ ...n, key: v })),

        setFieldWrapper: (v: string) =>
            patch((n) => ({ ...n, fieldConfig: { ...n.fieldConfig, fieldWrapper: v } })),

        setOutputType: (v: string) => patch((n) => ({ ...n, outputType: v as typeof n.outputType })),

        setDefault: (v: unknown) =>
            patch((n) => {
                if (n._kind !== "field") return n;
                // `undefined` = "no default" — delete the key so it never renders as 0 or "".
                if (v === undefined) {
                    const next = { ...n };
                    delete (next as { default?: unknown }).default;
                    return next;
                }
                return { ...n, default: v };
            }),

        setSlot: (v: string) =>
            patch((n) => ({ ...n, fieldConfig: { ...n.fieldConfig, slot: v || undefined } })),

        /**
         * `label` (the node's canvas display name) is edited from Basics now, not this array —
         * empty deletes the key rather than storing `""`, matching `OdataConfigForm`'s `setLabel`:
         * the canvas renders `wrapperProps?.label ?? node.key`, and an empty string would survive
         * that `??` and leave the node nameless.
         */
        setLabel: (label: string) =>
            patch((n) => {
                const next = { ...(n.fieldConfig.wrapperProps ?? {}) };
                if (label === "") delete next.label;
                else next.label = label;
                return { ...n, fieldConfig: { ...n.fieldConfig, wrapperProps: next } };
            }),

        /**
         * Flush wrapperProps field-array values to the node, coercing by declared type. `label`
         * lives outside the array (Basics owns it) — re-attached from the node afterwards so
         * this write never drops it, the same re-attach `flushControlProps` does for `options`.
         */
        flushWrapperProps: (entries: WrapperPropEntry[]) =>
            patch((n) => {
                const existingLabel = n.fieldConfig.wrapperProps?.label;
                const wp = toProps(entries);
                if (existingLabel !== undefined) wp.label = existingLabel;
                return { ...n, fieldConfig: { ...n.fieldConfig, wrapperProps: wp } };
            }),

        /** Flush controlProps field-array values to the node, preserving "options". */
        flushControlProps: (entries: WrapperPropEntry[]) =>
            patch((n) => {
                const existingOptions = n.fieldConfig.controlProps?.options;
                const cp = toProps(entries);
                if (existingOptions !== undefined) cp.options = existingOptions;
                return { ...n, fieldConfig: { ...n.fieldConfig, controlProps: cp } };
            }),

        /**
         * Flush a fully-formed controlProps object. The typed panel and the raw hatch both hand
         * the WHOLE object up (unlike the field-array `flushControlProps`); `options` rides along
         * inside `next` when present, so it survives without the field-array re-attach.
         */
        flushControlPropsObject: (next: Record<string, unknown>) =>
            patch((n) => ({ ...n, fieldConfig: { ...n.fieldConfig, controlProps: next } })),

        flushRules: (entries: RuleEntry[]) =>
            patch((n) => ({
                ...n,
                fieldConfig: {
                    ...n.fieldConfig,
                    rules: entries
                        .filter((e) => e.method)
                        .map((e) => ({
                            method: e.method,
                            ...(ruleTakesValue(e.method) && e.value !== ""
                                ? { value: coerceRuleValue(e.method, e.value) }
                                : {}),
                            ...(e.message ? { message: e.message } : {}),
                        })),
                },
            })),

        /** Flush the whole options list back to the node, preserving the other controlProps. */
        flushOptions: (next: ISelectOption[]) =>
            patch((n) => ({
                ...n,
                fieldConfig: {
                    ...n.fieldConfig,
                    controlProps: { ...n.fieldConfig.controlProps, options: next },
                },
            })),

        setCondition: (next: IConditionConfig | null) =>
            patch((n) => ({ ...n, fieldConfig: { ...n.fieldConfig, condition: next } })),

        /**
         * Write one event's script source into `fieldConfig.script`, immutably. An empty/
         * whitespace-only `source` removes that event's key instead of storing blank text;
         * if no events remain, `script` itself is cleared to `undefined` (mirrors `setCondition`
         * leaving no dangling empty object behind).
         */
        setScript: (event: string, source: string) =>
            patch((n) => {
                const prev = n.fieldConfig.script ?? {};
                const next: Record<string, string> = { ...prev };
                if (source.trim() === "") {
                    delete next[event];
                } else {
                    next[event] = source;
                }
                const hasAny = Object.keys(next).length > 0;
                return {
                    ...n,
                    fieldConfig: { ...n.fieldConfig, ...(hasAny ? { script: next } : { script: undefined }) },
                };
            }),
    };
}
