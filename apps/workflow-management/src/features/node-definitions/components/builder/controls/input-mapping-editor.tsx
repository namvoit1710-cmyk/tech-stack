import { ExpressionAutocompleteInput } from "@/components/expression-input/expression-autocomplete-input";
import { useLanguage } from "@/hooks/use-language";
import {
    createMappingEntry,
    type IMappingEntry,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import { inputExpressionSuggestions } from "@/features/node-definitions/lib/expression-suggestions";
import { type IWorkerFieldMeta } from "@/features/node-definitions/lib/worker-fields";
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { Input } from "@ldc/ui/components/input";
import { PlusIcon, SparklesIcon, TrashIcon } from "lucide-react";
import { useCallback, useMemo, useRef } from "react";

interface InputMappingEditorProps {
    entries: IMappingEntry[];
    onChange: (entries: IMappingEntry[]) => void;
    customFields: ISchemaFieldItem[];
    /** The base worker's mappable fields, resolved dynamically from its
     *  input_schema (or the curated registry). Drives the Add-Mapping menu. */
    workerFields: IWorkerFieldMeta[];
    /** Worker fields locked in Defaults — not offered here (Defaults owns them). */
    lockedKeys?: Set<string>;
}

/** Insert-a-custom-field chip row — a quick-insert complement to typing `{{`. */
const FieldChips = ({ fields, onInsert, tiny }: { fields: ISchemaFieldItem[]; onInsert: (key: string, type: string) => void; tiny?: boolean }) => {
    if (!fields.length) return null;
    return (
        <div className="flex flex-wrap gap-1">
            {fields.map((f) => (
                <button
                    key={f.key}
                    type="button"
                    onClick={() => onInsert(f.key, f.outputType)}
                    className={cn(
                        "bg-primary/10 text-primary rounded hover:bg-primary/15 font-mono",
                        tiny ? "text-[10px] px-1 py-0 leading-4" : "text-xs px-1.5 py-0.5"
                    )}
                >
                    {f.label || f.key}
                </button>
            ))}
        </div>
    );
};

const ExpressionEditor = ({ value, onChange, customFields, suggestions }: { value: string; onChange: (v: string) => void; customFields: ISchemaFieldItem[]; suggestions: IExprSuggestion[] }) => (
    <div className="flex flex-col gap-1">
        <ExpressionAutocompleteInput value={value} onChange={onChange} suggestions={suggestions} placeholder="value or {{$custom.field}}" className="h-8 text-sm" />
        <FieldChips fields={customFields} onInsert={(key) => onChange(value + `{{$custom.${key}}}`)} />
    </div>
);

const KeyValueArrayEditor = ({ items, onChange, meta, customFields, suggestions }: { items: { name: string; value: string }[]; onChange: (i: { name: string; value: string }[]) => void; meta: IWorkerFieldMeta; customFields: ISchemaFieldItem[]; suggestions: IExprSuggestion[] }) => {
    const { t } = useLanguage();
    const update = (idx: number, field: "name" | "value", val: string) => {
        const next = [...items];
        next[idx] = { ...next[idx], [field]: val } as { name: string; value: string };
        onChange(next);
    };
    return (
        <div className="flex flex-col gap-2">
            {items.length > 0 && (
                <div className="border border-border/60 rounded-md overflow-hidden">
                    <div className="grid grid-cols-[1fr_1fr_32px] bg-muted/40 px-2 py-1 border-b border-border/60">
                        <span className="text-xs text-muted-foreground">{meta.nameLabel || "Name"}</span>
                        <span className="text-xs text-muted-foreground">{meta.valueLabel || "Value"}</span>
                        <span />
                    </div>
                    {items.map((item, idx) => (
                        <div key={idx} className="grid grid-cols-[1fr_1fr_32px] gap-1.5 px-2 py-1.5 items-center border-b border-border/60 last:border-0">
                            <Input value={item.name} onChange={(e) => update(idx, "name", e.target.value)} placeholder={meta.nameKey} className="h-7 text-xs" />
                            <div className="flex flex-col gap-0.5">
                                <ExpressionAutocompleteInput value={item.value} onChange={(v) => update(idx, "value", v)} suggestions={suggestions} placeholder="{{$custom.field}}" className="h-7 text-xs" />
                                <FieldChips fields={customFields} onInsert={(key) => update(idx, "value", `{{$custom.${key}}}`)} tiny />
                            </div>
                            <Button variant="ghost" size="icon" className="size-6 text-destructive hover:text-destructive" onClick={() => onChange(items.filter((_, i) => i !== idx))} aria-label={t("node_definition_builder.remove_field")}>
                                <TrashIcon className="size-3" />
                            </Button>
                        </div>
                    ))}
                </div>
            )}
            <Button variant="outline" size="sm" className="self-start h-7 text-xs" onClick={() => onChange([...items, { name: "", value: "" }])}>
                <PlusIcon className="size-3 mr-1" />
                {t("node_definition_builder.add_entry")}
            </Button>
        </div>
    );
};

const JsonTemplateEditor = ({ value, onChange, customFields, suggestions }: { value: string; onChange: (v: string) => void; customFields: ISchemaFieldItem[]; suggestions: IExprSuggestion[] }) => {
    const { t } = useLanguage();
    const ref = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

    const insert = (key: string, type: string) => {
        const expr = type === "number" || type === "boolean" ? `{{$custom.${key}}}` : `"{{$custom.${key}}}"`;
        const el = ref.current;
        if (el) {
            const start = el.selectionStart ?? value.length;
            const end = el.selectionEnd ?? value.length;
            onChange(value.slice(0, start) + expr + value.slice(end));
            requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + expr.length; el.focus(); });
        } else {
            onChange(value + expr);
        }
    };

    const generate = () => {
        if (!customFields.length) return;
        const lines = customFields.filter((f) => f.key).map((f) => {
            const numOrBool = f.outputType === "number" || f.outputType === "boolean";
            return numOrBool ? `  "${f.key}": {{$custom.${f.key}}}` : `  "${f.key}": "{{$custom.${f.key}}}"`;
        });
        onChange(`{\n${lines.join(",\n")}\n}`);
    };

    return (
        <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground">{t("node_definition_builder.insert_field")}:</span>
                <FieldChips fields={customFields} onInsert={insert} />
                {customFields.length > 0 && (
                    <Button variant="outline" size="sm" className="h-6 text-xs ml-auto" onClick={generate}>
                        <SparklesIcon className="size-3 mr-1" />
                        {t("node_definition_builder.auto_generate")}
                    </Button>
                )}
            </div>
            <ExpressionAutocompleteInput
                ref={ref}
                multiline
                rows={5}
                value={value}
                onChange={onChange}
                suggestions={suggestions}
                placeholder={'{ "key": "{{$custom.field}}" }'}
                className="text-xs min-h-20"
            />
        </div>
    );
};

const InputMappingEditor = ({ entries, onChange, customFields, workerFields, lockedKeys }: InputMappingEditorProps) => {
    const { t } = useLanguage();

    const suggestions = useMemo<IExprSuggestion[]>(
        () => inputExpressionSuggestions(customFields, {
            custom: t("node_definition_builder.expr_group_custom"),
            input: t("node_definition_builder.expr_group_input"),
            inputDesc: t("node_definition_builder.expr_input_hint"),
        }),
        [customFields, t]
    );

    const availableFields = useMemo(() => {
        const used = new Set(entries.map((e) => e.workerField));
        return workerFields.filter((f) => !used.has(f.key) && !lockedKeys?.has(f.key));
    }, [entries, workerFields, lockedKeys]);

    const add = useCallback(
        (key: string) => onChange([...entries, createMappingEntry(key, workerFields.find((f) => f.key === key))]),
        [entries, onChange, workerFields]
    );
    const update = useCallback(
        (idx: number, partial: Partial<IMappingEntry>) => {
            const next = [...entries];
            next[idx] = { ...next[idx], ...partial } as IMappingEntry;
            onChange(next);
        },
        [entries, onChange]
    );

    return (
        <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{t("node_definition_builder.input_mapping_desc")}</p>
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="outline" size="sm" disabled={availableFields.length === 0}>
                            <PlusIcon className="size-3 mr-1" />
                            {t("node_definition_builder.add_mapping")}
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="max-h-64 overflow-y-auto w-64">
                        {availableFields.map((field) => (
                            <DropdownMenuItem key={field.key} onSelect={() => add(field.key)} className="flex flex-col items-start">
                                <span className="text-sm font-medium">{field.label}</span>
                                <span className="text-xs text-muted-foreground font-mono">{field.key}</span>
                            </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>

            {!!lockedKeys?.size && (
                <p className="text-xs text-muted-foreground">{t("node_definition_builder.mapping_locked_note")}</p>
            )}

            {entries.length === 0 && (
                <div className="text-center py-8 text-sm text-muted-foreground border border-dashed border-border rounded-lg">
                    {t("node_definition_builder.no_mappings")}
                </div>
            )}

            {entries.map((entry, idx) => {
                const meta = workerFields.find((f) => f.key === entry.workerField);
                const mode = meta?.mode ?? "expression";
                return (
                    <div key={entry.workerField} className="border border-border rounded-lg bg-card overflow-hidden">
                        <div className="flex items-center justify-between px-3 py-2 bg-muted/40 border-b border-border">
                            <div className="flex items-center gap-2">
                                <span className={cn(
                                    "text-[10px] uppercase px-1.5 py-0.5 rounded font-medium",
                                    mode === "key_value_array" && "bg-purple-100 text-purple-700",
                                    mode === "json_template" && "bg-amber-100 text-amber-700",
                                    mode === "expression" && "bg-primary/15 text-primary",
                                )}>
                                    {mode === "key_value_array" ? "Array" : mode === "json_template" ? "JSON" : "Value"}
                                </span>
                                <div className="flex flex-col">
                                    <span className="text-sm font-medium">{meta?.label || entry.workerField}</span>
                                    <span className="text-xs text-muted-foreground font-mono">{entry.workerField}</span>
                                </div>
                            </div>
                            <Button variant="ghost" size="icon" className="size-7 text-destructive hover:text-destructive" onClick={() => onChange(entries.filter((_, i) => i !== idx))} aria-label={t("node_definition_builder.remove_field")}>
                                <TrashIcon className="size-3" />
                            </Button>
                        </div>
                        <div className="px-3 py-3">
                            {mode === "expression" && <ExpressionEditor value={entry.expressionValue} onChange={(v) => update(idx, { expressionValue: v })} customFields={customFields} suggestions={suggestions} />}
                            {mode === "key_value_array" && meta && <KeyValueArrayEditor items={entry.arrayItems} onChange={(items) => update(idx, { arrayItems: items })} meta={meta} customFields={customFields} suggestions={suggestions} />}
                            {mode === "json_template" && <JsonTemplateEditor value={entry.jsonTemplate} onChange={(v) => update(idx, { jsonTemplate: v })} customFields={customFields} suggestions={suggestions} />}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

export default InputMappingEditor;
