import type { FieldArrayWithId, UseFormReturn } from "react-hook-form";

import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

import LdcCodeEditor from "../../../../field-control/code-control";
import { COMPACT_FIELD_CLASS, ItemCard, ItemList } from "../shared/item-card";
import { PropKeyAutocomplete } from "../shared/prop-key-autocomplete";
import type { ConfigFormValues, WpType } from "../shared/prop-value-utils";
import { WP_TYPES } from "../shared/prop-value-utils";

/**
 * The two key/value/type lists the field form renders — `wrapperProps` and `controlProps`.
 * One `ItemCard` per row (Key + Type inline in the header, Value full-width below), inside one
 * `ItemList` (empty state + full-width dashed Add). `label` no longer pins a row here — SA-2156
 * moved it to Basics, so both variants are now IDENTICAL except their title/copy.
 *
 * The rows stay in the CALLER's `useFieldArray` — this component owns no state and adds no
 * hooks. It reads and writes through the caller's `form`, so `register` keeps the value
 * inputs uncontrolled exactly as before and `Add` still appends to the field array WITHOUT
 * flushing to the document (a characterization test pins that: the document changes when
 * the user TYPES, not when a blank row appears).
 */
type Variant = "wrapper" | "control";

const VARIANTS: Record<Variant, { title: string; addLabel: string; emptyLabel: string }> = {
    wrapper: { title: "Wrapper Props", addLabel: "+ Add wrapper prop", emptyLabel: "No wrapper props yet" },
    control: { title: "Control Props", addLabel: "+ Add control prop", emptyLabel: "No control props yet" },
};

export interface PropsGridProps {
    variant: Variant;
    /** The field form's react-hook-form instance — the grid drives it, it does not own it. */
    form: UseFormReturn<ConfigFormValues>;
    /** Which field array to render. Both hold `WrapperPropEntry[]`. */
    name: "wrapperProps" | "controlProps";
    /** `useFieldArray`'s `fields` — the caller keeps the array hook. */
    fields: FieldArrayWithId<ConfigFormValues, "wrapperProps" | "controlProps", "id">[];
    suggestions: string[];
    onAdd: () => void;
    onRemove: (index: number) => void;
    /** Push the field array's current values to the node. */
    onFlush: () => void;
}

export function PropsGrid({ variant, form, name, fields, suggestions, onAdd, onRemove, onFlush }: PropsGridProps) {
    const { register, setValue, watch } = form;
    const { title, addLabel, emptyLabel } = VARIANTS[variant];

    /**
     * `wrapperProps` and `controlProps` hold the SAME element type, so a path into either is
     * structurally a path into the other. TypeScript will not narrow a union of two
     * field-array paths on its own (`setValue` collapses to `never`), so the path is built
     * once and typed as the `wrapperProps` branch. Sound at runtime — the shapes are equal.
     */
    const path = <P extends "key" | "value" | "type">(i: number, part: P) =>
        `${name}.${i}.${part}` as `wrapperProps.${number}.${P}`;

    return (
        <div className="mb-1">
            <p className="text-[10px] font-medium text-muted-foreground mb-1.5">{title}</p>
            <ItemList isEmpty={fields.length === 0} emptyLabel={emptyLabel} addLabel={addLabel} onAdd={onAdd}>
                {fields.map((field, i) => {
                    const entryType = watch(path(i, "type"));
                    const isJson = entryType === "json";
                    return (
                        <ItemCard
                            key={field.id}
                            onRemove={() => onRemove(i)}
                            header={
                                <>
                                    <PropKeyAutocomplete
                                        value={watch(path(i, "key"))}
                                        suggestions={suggestions}
                                        onChange={(next) => {
                                            setValue(path(i, "key"), next);
                                            onFlush();
                                        }}
                                    />
                                    <Select
                                        value={entryType}
                                        onValueChange={(v) => {
                                            setValue(path(i, "type"), v as WpType);
                                            onFlush();
                                        }}
                                    >
                                        <SelectTrigger aria-label="Type" size="sm" className="text-[10px] w-[72px] shrink-0 px-1.5">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {WP_TYPES.map((t) => (
                                                <SelectItem key={t} value={t} className="text-xs!">{t}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </>
                            }
                        >
                            {isJson ? (
                                <LdcCodeEditor
                                    language="json"
                                    value={watch(path(i, "value"))}
                                    onChange={(v) => {
                                        setValue(path(i, "value"), v);
                                        onFlush();
                                    }}
                                    minHeight={80}
                                    maxHeight={240}
                                />
                            ) : (
                                <Input
                                    {...register(path(i, "value"), { onChange: () => onFlush() })}
                                    placeholder={entryType === "boolean" ? "true / false" : "value"}
                                    className={`${COMPACT_FIELD_CLASS} w-full`}
                                />
                            )}
                        </ItemCard>
                    );
                })}
            </ItemList>
        </div>
    );
}

export default PropsGrid;
