import { X } from "lucide-react";
import { useState } from "react";

import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";

import type { IConditionConfig } from "../../../../../types/condition";
import { EShowWhenOperator } from "../../../../../types/condition";
import type { OutputType } from "../../../../../types/schema";
import { OPERATOR_OPTIONS } from "../../../constants";
import { coerceConditionValue, conditionValueKind } from "../../../utils/condition-utils";
import { CollapsibleSection } from "../shared/collapsible-section";
import { COMPACT_FIELD_CLASS, ItemCard, SECTION_ROWS_PADDING_CLASS } from "../shared/item-card";
import { stringifyValue } from "../shared/prop-value-utils";

export interface ConditionEditorProps {
    condition: IConditionConfig | null;
    /** The selected node's sibling fields, so Watch field is a typed dropdown, not a free string. */
    siblingFields?: { key: string; outputType: OutputType }[];
    /** `null` clears the condition entirely. */
    onChange: (next: IConditionConfig | null) => void;
}

/** The seed a brand-new condition is built from (same as the old inline panel: Equals / invisible / ""). */
const EMPTY: IConditionConfig = {
    fieldKey: "",
    operator: EShowWhenOperator.Equals,
    action: "invisible",
    conditionValue: "",
};

/** Render the stored value back into the raw form each input reads (arrays → comma string). */
const rawString = (v: unknown): string => (Array.isArray(v) ? v.map(String).join(", ") : stringifyValue(v));

/**
 * The Condition section — a single `ItemCard` rule, matching `use-check-condittion.ts` exactly.
 *
 * The value input is chosen by `conditionValueKind(operator, watched-outputType)` and the value is
 * coerced by `coerceConditionValue` on write, so the runtime's strict `===` / `Array.isArray` /
 * `Number()` comparators actually fire — unchanged from before this ticket.
 *
 * New: the value can also be BOUND to another field's live value instead of a static literal
 * (`valueSource: "field"` + `valueFieldKey`), via a compact Static | From field toggle. Static
 * stays the default (`valueSource` undefined reads as static), so every existing condition is
 * unchanged.
 */
export function ConditionEditor({ condition, siblingFields = [], onChange }: ConditionEditorProps) {
    const cond = condition ?? EMPTY;
    const isFromField = cond.valueSource === "field";
    const watchedType = siblingFields.find((f) => f.key === cond.fieldKey)?.outputType;
    const kind = conditionValueKind(cond.operator, watchedType);

    /** Re-coerce the current STATIC value whenever the operator or watched field changes its type. */
    const reseat = (next: IConditionConfig): IConditionConfig => ({
        ...next,
        conditionValue:
            next.valueSource === "field"
                ? next.conditionValue
                : coerceConditionValue(
                      next.operator,
                      siblingFields.find((f) => f.key === next.fieldKey)?.outputType,
                      rawString(next.conditionValue),
                  ),
    });

    const setFieldKey = (key: string) => onChange(reseat({ ...cond, fieldKey: key }));
    const setOperator = (op: EShowWhenOperator) => onChange(reseat({ ...cond, operator: op }));
    const setAction = (a: IConditionConfig["action"]) => onChange({ ...cond, action: a });
    const setValueRaw = (raw: string | string[]) =>
        onChange({ ...cond, conditionValue: coerceConditionValue(cond.operator, watchedType, raw) });
    const setValueSource = (source: "static" | "field") =>
        onChange({
            ...cond,
            valueSource: source,
            conditionValue: source === "field" ? "" : cond.conditionValue,
            valueFieldKey: source === "field" ? cond.valueFieldKey : undefined,
        });
    const setValueFieldKey = (key: string) => onChange({ ...cond, valueFieldKey: key });

    return (
        <CollapsibleSection label="Condition">
            <div className={SECTION_ROWS_PADDING_CLASS}>
                <ItemCard onRemove={cond.fieldKey ? () => onChange(null) : undefined} removeLabel="Remove condition">
                    <div className="flex flex-col gap-1.5">
                        <label className="text-[10px] font-medium text-muted-foreground">Watch field</label>
                        {siblingFields.length === 0 ? (
                            <p className="text-[11px] text-muted-foreground px-0.5">No sibling fields to watch.</p>
                        ) : (
                            <Select value={cond.fieldKey || undefined} onValueChange={setFieldKey}>
                                <SelectTrigger aria-label="Watch field" size="sm" className="text-xs! w-full">
                                    <SelectValue placeholder="Select a field" />
                                </SelectTrigger>
                                <SelectContent>
                                    {siblingFields.map((f) => (
                                        <SelectItem key={f.key} value={f.key} className="text-xs!">
                                            {f.key} <span className="text-muted-foreground">· {f.outputType}</span>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                    </div>

                    {cond.fieldKey && (
                        <>
                            <div className="grid grid-cols-2 gap-2">
                                <div className="flex flex-col gap-1.5">
                                    <label className="text-[10px] font-medium text-muted-foreground">Operator</label>
                                    <Select value={cond.operator} onValueChange={(v) => setOperator(v as EShowWhenOperator)}>
                                        <SelectTrigger aria-label="Operator" size="sm" className="text-xs! w-full">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {OPERATOR_OPTIONS.map((o) => (
                                                <SelectItem key={o.value} value={o.value} className="text-xs!">{o.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="flex flex-col gap-1.5">
                                    <label className="text-[10px] font-medium text-muted-foreground">Action</label>
                                    <Select value={cond.action} onValueChange={(v) => setAction(v as IConditionConfig["action"])}>
                                        <SelectTrigger aria-label="Action" size="sm" className="text-xs! w-full">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="invisible" className="text-xs!">invisible</SelectItem>
                                            <SelectItem value="disabled" className="text-xs!">disabled</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            {kind === "none" ? (
                                <p className="text-[11px] text-muted-foreground">
                                    This operator compares only truthiness — no value needed.
                                </p>
                            ) : (
                                <div className="flex flex-col gap-1.5">
                                    <div className="flex items-center justify-between gap-2">
                                        <label className="text-[10px] font-medium text-muted-foreground">Value</label>
                                        <ToggleGroup
                                            type="single"
                                            value={cond.valueSource ?? "static"}
                                            onValueChange={(v) => v && setValueSource(v as "static" | "field")}
                                            variant="outline"
                                            size="sm"
                                        >
                                            <ToggleGroupItem value="static" className="h-7 text-[10px]">Static</ToggleGroupItem>
                                            <ToggleGroupItem value="field" className="h-7 text-[10px]">From field</ToggleGroupItem>
                                        </ToggleGroup>
                                    </div>

                                    {isFromField ? (
                                        <Select value={cond.valueFieldKey} onValueChange={setValueFieldKey}>
                                            <SelectTrigger aria-label="Value field" size="sm" className="text-xs! w-full">
                                                <SelectValue placeholder="Select a field" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {siblingFields
                                                    .filter((f) => f.key !== cond.fieldKey)
                                                    .map((f) => (
                                                        <SelectItem key={f.key} value={f.key} className="text-xs!">
                                                            {f.key} <span className="text-muted-foreground">· {f.outputType}</span>
                                                        </SelectItem>
                                                    ))}
                                            </SelectContent>
                                        </Select>
                                    ) : kind === "list" ? (
                                        <ConditionListInput
                                            values={Array.isArray(cond.conditionValue) ? cond.conditionValue.map(String) : []}
                                            onChange={setValueRaw}
                                        />
                                    ) : kind === "boolean" ? (
                                        <Select value={String(cond.conditionValue)} onValueChange={setValueRaw}>
                                            <SelectTrigger aria-label="Condition value" size="sm" className="text-xs! w-full">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="true" className="text-xs!">true</SelectItem>
                                                <SelectItem value="false" className="text-xs!">false</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    ) : (
                                        <Input
                                            aria-label="Condition value"
                                            type={kind === "number" ? "number" : "text"}
                                            className={`${COMPACT_FIELD_CLASS} w-full`}
                                            placeholder="value to compare"
                                            value={rawString(cond.conditionValue)}
                                            onChange={(e) => setValueRaw(e.target.value)}
                                        />
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </ItemCard>
            </div>
        </CollapsibleSection>
    );
}

/** A tiny chips input for the `In` operator — commits on Enter/comma/blur, each chip removable. */
function ConditionListInput({ values, onChange }: { values: string[]; onChange: (v: string[]) => void }) {
    const [draft, setDraft] = useState("");
    const add = () => {
        const v = draft.trim();
        if (!v) return;
        onChange([...values, v]);
        setDraft("");
    };
    return (
        <div className={`flex flex-wrap items-center gap-1 border border-border rounded-md px-2 py-1 min-h-8`}>
            {values.map((v, i) => (
                <span key={`${v}-${i}`} className="inline-flex items-center gap-1 bg-muted rounded px-1.5 py-0.5 text-xs">
                    {v}
                    <button type="button" aria-label={`Remove ${v}`} onMouseDown={(e) => e.preventDefault()} onClick={() => onChange(values.filter((_, idx) => idx !== i))}>
                        <X className="w-3 h-3" />
                    </button>
                </span>
            ))}
            <input
                className="flex-1 min-w-16 bg-transparent outline-none text-xs h-6"
                aria-label="Add condition value"
                value={draft}
                placeholder="add value…"
                onChange={(e) => setDraft(e.target.value)}
                onBlur={add}
                onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                        e.preventDefault();
                        add();
                    }
                }}
            />
        </div>
    );
}

export default ConditionEditor;
