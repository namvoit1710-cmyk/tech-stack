import { ExpressionAutocompleteInput } from "@/components/expression-input/expression-autocomplete-input";
import { useLanguage } from "@/hooks/use-language";
import { type IKeyValuePair, type ISuggestion } from "@/features/node-definitions/lib/converters";
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { PlusIcon, TrashIcon } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

interface OutputMappingEditorProps {
    pairs: IKeyValuePair[];
    onChange: (pairs: IKeyValuePair[]) => void;
    /** Plain-substring suggestions for the output-name (key) field. */
    keySuggestions?: ISuggestion[];
    /** `{{$_output.*}}` expression suggestions for the value field. */
    valueSuggestions?: IExprSuggestion[];
}

/** Simple substring autocomplete for the output-name field (not an expression). */
const NameAutocomplete = ({ value, onChange, placeholder, suggestions }: { value: string; onChange: (v: string) => void; placeholder?: string; suggestions?: ISuggestion[] }) => {
    const [open, setOpen] = useState(false);
    const [focused, setFocused] = useState(-1);
    const containerRef = useRef<HTMLDivElement>(null);

    const filtered = useMemo(
        () => suggestions?.filter((s) => !value || s.value.toLowerCase().includes(value.toLowerCase()) || s.label.toLowerCase().includes(value.toLowerCase())) ?? [],
        [suggestions, value]
    );
    const hasSuggestions = !!suggestions?.length;
    const select = (s: ISuggestion) => { onChange(s.value); setOpen(false); setFocused(-1); };

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (!open || !filtered.length) return;
        if (e.key === "ArrowDown") { e.preventDefault(); setFocused((p) => Math.min(p + 1, filtered.length - 1)); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setFocused((p) => Math.max(p - 1, 0)); }
        else if (e.key === "Enter" && focused >= 0) { e.preventDefault(); select(filtered[focused]!); }
        else if (e.key === "Escape") setOpen(false);
    };

    return (
        <div className="relative" ref={containerRef}>
            <Input
                value={value}
                onChange={(e) => { onChange(e.target.value); if (hasSuggestions) setOpen(true); setFocused(-1); }}
                onFocus={() => hasSuggestions && setOpen(true)}
                onBlur={(e) => { if (!containerRef.current?.contains(e.relatedTarget as Node)) setOpen(false); }}
                onKeyDown={onKeyDown}
                placeholder={placeholder}
                className="h-8 text-sm"
            />
            {open && filtered.length > 0 && (
                <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-card border border-border rounded-md shadow-lg max-h-48 overflow-y-auto">
                    {filtered.map((s, idx) => (
                        <button
                            key={s.value}
                            type="button"
                            className={cn("w-full text-left px-3 py-1.5 text-sm hover:bg-primary/10 flex flex-col", idx === focused && "bg-primary/10")}
                            onMouseDown={(e) => { e.preventDefault(); select(s); }}
                        >
                            <span className="font-mono text-xs text-primary">{s.value}</span>
                            {s.description && <span className="text-xs text-muted-foreground">{s.description}</span>}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
};

/** Output mapping: rename/extract worker output into named run-context variables. */
const OutputMappingEditor = ({ pairs, onChange, keySuggestions, valueSuggestions = [] }: OutputMappingEditorProps) => {
    const { t } = useLanguage();

    const change = useCallback(
        (index: number, field: "key" | "value", val: string) => {
            const next = [...pairs];
            next[index] = { ...next[index], [field]: val } as IKeyValuePair;
            onChange(next);
        },
        [pairs, onChange]
    );

    return (
        <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{t("node_definition_builder.output_mapping_desc")}</p>
                <Button variant="outline" size="sm" onClick={() => onChange([...pairs, { key: "", value: "" }])}>
                    <PlusIcon className="size-3 mr-1" />
                    {t("node_definition_builder.add_entry")}
                </Button>
            </div>

            {pairs.length > 0 ? (
                <div className="border border-border rounded-lg overflow-hidden">
                    <div className="grid grid-cols-[1fr_1fr_40px] bg-muted/40 px-3 py-2 border-b border-border">
                        <span className="text-xs font-medium text-muted-foreground uppercase">{t("node_definition_builder.output_name")}</span>
                        <span className="text-xs font-medium text-muted-foreground uppercase">{t("node_definition_builder.expression")}</span>
                        <span />
                    </div>
                    {pairs.map((pair, index) => (
                        // Key by index only — keying on `pair.key` would remount the row
                        // (and drop input focus) on every keystroke in the output-name field.
                        <div key={index} className="grid grid-cols-[1fr_1fr_40px] gap-2 px-3 py-2 items-center border-b border-border/60 last:border-b-0">
                            <NameAutocomplete value={pair.key} onChange={(v) => change(index, "key", v)} placeholder="output_name" suggestions={keySuggestions} />
                            <ExpressionAutocompleteInput value={pair.value} onChange={(v) => change(index, "value", v)} suggestions={valueSuggestions} placeholder="{{$_output.response_body}}" className="h-8 text-sm" />
                            <Button variant="ghost" size="icon" className="size-7 text-destructive hover:text-destructive" onClick={() => onChange(pairs.filter((_, i) => i !== index))} aria-label={t("node_definition_builder.remove_field")}>
                                <TrashIcon className="size-3" />
                            </Button>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="text-center py-6 text-sm text-muted-foreground border border-dashed border-border rounded-lg">
                    {t("node_definition_builder.no_entries")}
                </div>
            )}
        </div>
    );
};

export default OutputMappingEditor;
