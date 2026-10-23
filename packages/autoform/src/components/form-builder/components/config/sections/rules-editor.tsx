import { useEffect } from "react";
import { useFieldArray, useForm } from "react-hook-form";

import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

import type { IValidationMethod } from "../../../../../types/validation";
import { RULE_METHODS, isRuleEnforced, ruleValueKind } from "../../../utils/rules-utils";
import { CollapsibleSection } from "../shared/collapsible-section";
import { COMPACT_FIELD_CLASS, ItemCard, ItemList, SECTION_ROWS_PADDING_CLASS } from "../shared/item-card";
import type { RuleEntry } from "../shared/prop-value-utils";

export interface RulesEditorProps {
    /** Re-seeds the field array when a DIFFERENT node is selected — and only then. */
    nodeId: string;
    /** The selected node's rules, as field-array entries. */
    rules: RuleEntry[];
    /** Receives the whole next list; the caller coerces it and flushes it to the node. */
    onChange: (next: RuleEntry[]) => void;
}

/**
 * The Validation section — a collapsible group (closed by default, badged with the rule count)
 * of `ItemCard` rows: Method + (for a short value) the value inline in the header, Expression /
 * Message full-width below.
 *
 * 🚨 `Add rule` does NOT flush. It is a bare `useFieldArray` append: it grows this
 * component's LOCAL field array and never calls `onChange`, so the document is untouched
 * until the new row is actually edited. Flushing on append would write a
 * `{ method: "required" }` rule the user never asked for. This is pinned by
 * `config-panel.characterization.test.tsx` ("'Add rule' appends a row to the panel but
 * does not write to the node until it is edited"), and it is the same contract the
 * un-flushed `Add` buttons on Wrapper Props / Control Props and `RawPropsEditor`'s
 * "does not write an empty-string key when a row is added" hold.
 *
 * The consequence is deliberate and preserved: a rule the user adds and never touches is
 * SILENTLY DROPPED when the node is re-selected, because the effect below replays the
 * field array from the node. That is the existing contract, not a bug to fix here.
 */
export function RulesEditor({ nodeId, rules, onChange }: RulesEditorProps) {
    const { register, control, setValue, getValues, watch } = useForm<{ rules: RuleEntry[] }>({
        defaultValues: { rules },
    });

    const { fields, append, remove, replace } = useFieldArray({ control, name: "rules" });

    // `nodeId` is the ONLY re-seed trigger — the same dependency list the field array had
    // in `config-panel.tsx` (`[node._id]`). Re-seeding on every `rules` change would throw
    // away the local row state (a blank appended row) on each keystroke: it is the write we
    // just made coming back to overwrite the row that made it.
    useEffect(() => {
        replace(rules);
    }, [nodeId]);

    const flush = () => onChange(getValues("rules"));

    return (
        <CollapsibleSection label="Validation" count={fields.length}>
            <div className={SECTION_ROWS_PADDING_CLASS}>
                <ItemList
                    isEmpty={fields.length === 0}
                    emptyLabel="No validation rules yet"
                    addLabel="Add rule"
                    // Local only — see the note above. NO onChange call.
                    onAdd={() => append({ method: "required", value: "", message: "" })}
                >
                    {fields.map((field, i) => {
                        const method = watch(`rules.${i}.method`);
                        const kind = ruleValueKind(method);
                        const enforced = isRuleEnforced(method);
                        return (
                            <ItemCard
                                key={field.id}
                                onRemove={() => {
                                    remove(i);
                                    onChange(getValues("rules").filter((_, idx) => idx !== i));
                                }}
                                header={
                                    <>
                                        <Select
                                            value={method}
                                            onValueChange={(v) => {
                                                setValue(`rules.${i}.method`, v as IValidationMethod);
                                                flush();
                                            }}
                                        >
                                            <SelectTrigger size="sm" className="text-xs! flex-1">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {RULE_METHODS.map((m) => (
                                                    <SelectItem key={m} value={m} className="text-xs!">
                                                        {m}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        {(kind === "number" || kind === "text") && (
                                            <Input
                                                {...register(`rules.${i}.value`, { onChange: () => flush() })}
                                                type={kind === "number" ? "number" : "text"}
                                                placeholder="value"
                                                className={`${COMPACT_FIELD_CLASS} w-24 shrink-0`}
                                            />
                                        )}
                                    </>
                                }
                            >
                                {kind === "expression" && (
                                    // refine/superRefine evaluate this as `return (<expr>)` — it MUST be captured.
                                    <Input
                                        {...register(`rules.${i}.value`, { onChange: () => flush() })}
                                        placeholder="expression, e.g. value > 5"
                                        className={`${COMPACT_FIELD_CLASS} w-full font-mono`}
                                    />
                                )}
                                {!enforced && (
                                    <p className="text-[10px] text-amber-600 dark:text-amber-500 px-0.5">
                                        Not enforced at runtime yet
                                    </p>
                                )}
                                <Input
                                    {...register(`rules.${i}.message`, { onChange: () => flush() })}
                                    placeholder="message (optional)"
                                    className={`${COMPACT_FIELD_CLASS} w-full`}
                                />
                            </ItemCard>
                        );
                    })}
                </ItemList>
            </div>
        </CollapsibleSection>
    );
}

export default RulesEditor;
