import { ExpressionAutocompleteInput } from "@/components/expression-input/expression-autocomplete-input";
import { useLanguage } from "@/hooks/use-language";
import {
    entriesToLockedDefaults,
    lockedDefaultsToEntries,
    type ILockedDefaultEntry,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import { inputExpressionSuggestions } from "@/features/node-definitions/lib/expression-suggestions";
import { INodeInputSchema } from "@/features/workflows/types/node-pallete";
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { LdcCodeEditor } from "@ldc/autoform";
import { cn } from "@ldc/ui";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { LockIcon, UnlockIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import SectionShell, { type SectionMode } from "../section-shell";
import AdvancedJsonEditor from "../advanced-json-editor";

interface DefaultsSectionProps {
    entries: ILockedDefaultEntry[];
    onChange: (entries: ILockedDefaultEntry[]) => void;
    /** The selected worker's input schema (drives the lock rows). */
    workerSchema?: INodeInputSchema[];
    /** The node's own input fields — for `{{$custom.*}}` autocomplete in string values. */
    customFields?: ISchemaFieldItem[];
    onValidityChange?: (valid: boolean) => void;
}

/** Locked defaults: a worker-schema-driven lock table + Advanced JSON. */
const DefaultsSection = ({ entries, onChange, workerSchema, customFields = [], onValidityChange }: DefaultsSectionProps) => {
    const { t } = useLanguage();
    const [mode, setMode] = useState<SectionMode>("guided");

    const suggestions = useMemo<IExprSuggestion[]>(
        () => inputExpressionSuggestions(customFields, {
            custom: t("node_definition_builder.expr_group_custom"),
            input: t("node_definition_builder.expr_group_input"),
            inputDesc: t("node_definition_builder.expr_input_hint"),
        }),
        [customFields, t]
    );

    const update = useCallback(
        (index: number, partial: Partial<ILockedDefaultEntry>) => {
            const next = [...entries];
            next[index] = { ...next[index], ...partial } as ILockedDefaultEntry;
            onChange(next);
        },
        [entries, onChange]
    );

    const lockedCount = useMemo(() => entries.filter((e) => e.locked).length, [entries]);

    const handleModeChange = (next: SectionMode) => {
        if (next === "guided") onValidityChange?.(true);
        setMode(next);
    };

    /** Resolve the editor language for a CodeControl row, mirroring the worker's
     *  own behavior: follow the sibling field named by `languageField` (e.g. the
     *  "language" select), falling back to the static `language` controlProp. */
    const resolveCodeLanguage = (entry: ILockedDefaultEntry): string => {
        const cp = entry.controlProps ?? {};
        const langKey = cp.languageField as string | undefined;
        const sibling = langKey ? entries.find((e) => e.key === langKey) : undefined;
        if (sibling && typeof sibling.value === "string" && sibling.value) return sibling.value;
        return (cp.language as string) || "javascript";
    };

    const renderCodeEditor = (entry: ILockedDefaultEntry, index: number) => {
        // ponytail: a CodeControl default is the node's script, not a user-
        // overridable value — always editable, and typing content *is* setting
        // the default, so auto-lock on non-empty (unlock when cleared). Without
        // this the editor was readOnly while unlocked AND unlocked entries are
        // dropped by entriesToLockedDefaults, so the script could never be saved
        // and came back empty on every reload.
        return (
            <LdcCodeEditor
                value={entry.value}
                onChange={(v) => update(index, { value: v, locked: v.trim().length > 0 })}
                language={resolveCodeLanguage(entry)}
                placeholder={entry.controlProps?.placeholder}
                minHeight={140}
            />
        );
    };

    const renderValue = (entry: ILockedDefaultEntry, index: number) => {
        const disabled = !entry.locked;
        if (entry.type === "boolean") {
            return (
                <Select value={entry.value || "false"} onValueChange={(value) => update(index, { value })} disabled={disabled}>
                    <SelectTrigger className={cn("h-8 text-sm w-full", disabled && "opacity-40")}><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="true">true</SelectItem>
                        <SelectItem value="false">false</SelectItem>
                    </SelectContent>
                </Select>
            );
        }
        if (entry.options?.length) {
            return (
                <Select value={entry.value} onValueChange={(value) => update(index, { value })} disabled={disabled}>
                    <SelectTrigger className={cn("h-8 text-sm w-full", disabled && "opacity-40")}>
                        <SelectValue placeholder={t("node_definition_builder.select_value")} />
                    </SelectTrigger>
                    <SelectContent>
                        {entry.options.map((opt) => <SelectItem key={opt.id} value={opt.id}>{opt.value}</SelectItem>)}
                    </SelectContent>
                </Select>
            );
        }
        if (entry.type === "number") {
            return (
                <Input
                    value={entry.value}
                    onChange={(e) => update(index, { value: e.target.value })}
                    className={cn("h-8 text-sm", disabled && "opacity-40")}
                    type="number"
                    disabled={disabled}
                />
            );
        }
        // string — supports {{$custom.*}} / {{$input.*}} expressions
        return (
            <ExpressionAutocompleteInput
                value={entry.value}
                onChange={(v) => update(index, { value: v })}
                suggestions={suggestions}
                disabled={disabled}
                className={cn("h-8 text-sm", disabled && "opacity-40")}
            />
        );
    };

    const guided = entries.length > 0 ? (
        <div className="rounded-xl border border-border overflow-hidden">
            <div className="grid grid-cols-[40px_1fr_100px_1fr] bg-muted/40 px-3 py-2 border-b border-border">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide text-center">{t("node_definition_builder.lock")}</span>
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("node_definition_builder.parameter")}</span>
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("node_definition_builder.default_type")}</span>
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{t("node_definition_builder.fixed_value")}</span>
            </div>
            {entries.map((entry, index) => {
                const isCode = entry.controlType === "CodeControl";
                return (
                    <div key={entry.key} className={cn("border-b border-border/60 last:border-b-0", entry.locked && "bg-primary/5")}>
                        <div className="grid grid-cols-[40px_1fr_100px_1fr] gap-2 px-3 py-2 items-center">
                            <button
                                type="button"
                                onClick={() => update(index, { locked: !entry.locked })}
                                aria-pressed={entry.locked}
                                aria-label={entry.locked ? t("node_definition_builder.click_to_unlock") : t("node_definition_builder.click_to_lock")}
                                className={cn("flex items-center justify-center size-7 rounded-md mx-auto transition-colors", entry.locked ? "text-primary bg-primary/10 hover:bg-primary/15" : "text-muted-foreground hover:bg-muted")}
                            >
                                {entry.locked ? <LockIcon className="size-3.5" /> : <UnlockIcon className="size-3.5" />}
                            </button>
                            <div className="flex flex-col min-w-0">
                                <span className={cn("text-sm truncate", entry.locked ? "font-medium text-foreground" : "text-muted-foreground")}>{entry.label}</span>
                                <span className="text-xs text-muted-foreground truncate">{entry.key}</span>
                            </div>
                            <span className={cn("text-xs px-2 py-1 rounded-md text-center", entry.locked ? "bg-muted text-muted-foreground" : "text-muted-foreground/60")}>{entry.type}</span>
                            {isCode
                                ? <span className="text-xs text-muted-foreground italic">{t("node_definition_builder.code_editor_below")}</span>
                                : renderValue(entry, index)}
                        </div>
                        {isCode && <div className="px-3 pb-3 pl-[52px]">{renderCodeEditor(entry, index)}</div>}
                    </div>
                );
            })}
        </div>
    ) : (
        <div className="text-center py-10 text-sm text-muted-foreground border border-dashed border-border rounded-xl">
            {t("node_definition_builder.no_worker_params")}
        </div>
    );

    const advanced = (
        <AdvancedJsonEditor
            key={mode}
            defaultValue={entriesToLockedDefaults(entries)}
            onChange={(parsed) => onChange(lockedDefaultsToEntries(parsed, workerSchema))}
            onValidityChange={onValidityChange}
            placeholder='{ "method": "GET", "timeout_seconds": 30 }'
        />
    );

    return (
        <SectionShell
            title={t("node_definition_builder.locked_defaults")}
            help={t("node_definition_builder.locked_defaults_desc")}
            icon={<LockIcon className="size-5" />}
            mode={mode}
            onModeChange={handleModeChange}
            actions={<span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{t("node_definition_builder.locked_count", { count: lockedCount, total: entries.length })}</span>}
            guided={guided}
            advanced={advanced}
        />
    );
};

export default DefaultsSection;
