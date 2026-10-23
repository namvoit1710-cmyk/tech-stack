import type React from "react";
import { Plus, X } from "lucide-react";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Switch } from "@ldc/ui/components/switch";

import type { PropSchema } from "../../../constants/prop-keys";
import LdcCodeEditor from "../../../../field-control/code-control";
import { OptionsEditor } from "./options-editor";
import type { ISelectOption } from "../../../../field-control/select-control";

/** Sentinel for "this prop is absent" — never written to the document. */
export const NOT_SET = "__not_set__";

/**
 * Coerce a stored prop value into the string an <Input> needs. The document is hand-authored
 * JSON, so a `string`/`number` prop can still arrive holding an object; render that as JSON
 * rather than "[object Object]".
 */
function toInputValue(value: unknown): string {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    try {
        return JSON.stringify(value) ?? "";
    } catch {
        return "";
    }
}

/**
 * Coerce a stored prop value into the text the JSON editor shows. An absent prop
 * (`undefined`/`null`) renders as an EMPTY editor — rendering `{}` would assert the document
 * holds an empty object when the truth is that it holds nothing at all.
 */
function toJsonValue(value: unknown): string {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value, null, 2) ?? "";
    } catch {
        return "";
    }
}

/** Help caption, rendered under any widget that declares `help`. */
export function Help({ text }: { text?: string }) {
    if (!text) return null;
    return <p className="text-[10px] text-muted-foreground">{text}</p>;
}

/**
 * Everything a widget needs to render one prop row. `current` is precomputed by the dispatcher
 * (`value[key] ?? default`; `??`, never `||`, so a stored `false` still beats a `true` default);
 * `set`/`unset` write the WHOLE next object up through `onChange`.
 */
export interface WidgetProps {
    prop: PropSchema;
    current: unknown;
    id: string;
    label: string;
    set: (key: string, next: unknown) => void;
    unset: (key: string) => void;
    /** Sibling field keys — only the `field-ref` widget consumes these (SA-2040). */
    fieldRefOptions?: string[];
}

/**
 * Switch — writes a real `true`/`false`, never the string "true". Shown TRUTHILY (`Boolean(current)`)
 * so a legacy string "true"/"false" (which the runtime reads truthily) reports as it actually runs;
 * touching it self-heals the value to a real boolean.
 */
export function BooleanWidget({ prop, current, id, label, set }: WidgetProps) {
    return (
        <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2">
                <label className="text-xs text-foreground" htmlFor={id}>
                    {label}
                </label>
                <Switch
                    id={id}
                    aria-label={label}
                    checked={Boolean(current)}
                    onCheckedChange={(checked: boolean) => set(prop.key, checked)}
                />
            </div>
            <Help text={prop.help} />
        </div>
    );
}

/** Select of exactly the legal values plus "Not set", which DELETES the key (absence is meaningful). */
export function EnumWidget({ prop, current, id, label, set, unset }: WidgetProps) {
    return (
        <div className="flex flex-col gap-1">
            <label htmlFor={id} className="text-[10px] font-medium text-muted-foreground">
                {label}
            </label>
            <Select
                value={typeof current === "string" ? current : NOT_SET}
                onValueChange={(v: string) => (v === NOT_SET ? unset(prop.key) : set(prop.key, v))}
            >
                <SelectTrigger id={id} aria-label={label} className="h-8! w-full text-xs!">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value={NOT_SET} className="text-xs!">
                        Not set
                    </SelectItem>
                    {(prop.options ?? []).map((o) => (
                        <SelectItem key={o} value={o} className="text-xs!">
                            {o}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
            <Help text={prop.help} />
        </div>
    );
}

/** Monaco JSON editor; absent renders EMPTY, not `{}`. Named via its labelled group. */
export function JsonWidget({ prop, current, id, label, set }: WidgetProps) {
    return (
        <div className="flex flex-col gap-1">
            <label id={`${id}-label`} className="text-[10px] font-medium text-muted-foreground">
                {label}
            </label>
            <div id={id} role="group" aria-labelledby={`${id}-label`}>
                <LdcCodeEditor
                    language="json"
                    value={toJsonValue(current)}
                    onChange={(v: string) => {
                        try {
                            set(prop.key, JSON.parse(v));
                        } catch {
                            // Mid-keystroke the text is not valid JSON yet; keep the raw string
                            // or the editor becomes untypeable.
                            set(prop.key, v);
                        }
                    }}
                    minHeight={80}
                    maxHeight={240}
                />
            </div>
            <Help text={prop.help} />
        </div>
    );
}

/** Input — `type=number` for numbers. Clearing a number DELETES the key (never writes `0`). */
export function ScalarWidget({ prop, current, id, label, set, unset }: WidgetProps) {
    return (
        <div className="flex flex-col gap-1">
            <label htmlFor={id} className="text-[10px] font-medium text-muted-foreground">
                {label}
            </label>
            <Input
                id={id}
                aria-label={label}
                type={prop.type === "number" ? "number" : "text"}
                className="h-8! text-xs!"
                value={toInputValue(current)}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    const raw = e.target.value;
                    if (prop.type !== "number") {
                        set(prop.key, raw);
                        return;
                    }
                    // `Number("") === 0`, so an emptied box would silently write a real 0.
                    // Clearing means "not set" — same delete path the enum's "Not set" uses.
                    if (raw.trim() === "") {
                        unset(prop.key);
                        return;
                    }
                    set(prop.key, Number(raw));
                }}
            />
            <Help text={prop.help} />
        </div>
    );
}

/** Wraps the existing `OptionsEditor` (id/value rows) for a static Choice options list. */
export function OptionsWidget({ prop, current, set }: WidgetProps) {
    const list = Array.isArray(current) ? (current as ISelectOption[]) : [];
    return (
        <div>
            <OptionsEditor options={list} onChange={(next) => set(prop.key, next)} />
        </div>
    );
}

/** String[] editor with add/remove rows — e.g. `serverOptions.dependencies`. */
export function ListWidget({ prop, current, label, set }: WidgetProps) {
    const items = Array.isArray(current) ? (current as unknown[]).map((v) => String(v ?? "")) : [];
    const write = (next: string[]) => set(prop.key, next);
    return (
        <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium text-muted-foreground">{label}</label>
            <div className="space-y-1">
                {items.map((item, i) => (
                    <div key={i} className="flex items-center gap-1">
                        <Input
                            aria-label={`${label} ${i + 1}`}
                            className="h-8! text-xs! flex-1"
                            value={item}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                write(items.map((v, idx) => (idx === i ? e.target.value : v)))
                            }
                        />
                        <button
                            type="button"
                            aria-label={`Remove ${label} ${i + 1}`}
                            onClick={() => write(items.filter((_, idx) => idx !== i))}
                            className="p-1 text-muted-foreground hover:text-destructive"
                        >
                            <X className="w-3 h-3" />
                        </button>
                    </div>
                ))}
                <button
                    type="button"
                    onClick={() => write([...items, ""])}
                    className="flex items-center gap-1 text-xs! text-primary hover:underline mt-1"
                >
                    <Plus className="w-3 h-3" /> Add {label.toLowerCase()}
                </button>
            </div>
            <Help text={prop.help} />
        </div>
    );
}

/** key -> value map editor with add/remove rows — e.g. `serverOptions.params`/`queryParams`/`body`. */
export function KeyValueWidget({ prop, current, label, set }: WidgetProps) {
    const map =
        current && typeof current === "object" && !Array.isArray(current)
            ? (current as Record<string, unknown>)
            : {};
    const entries = Object.entries(map);
    const writeEntries = (next: [string, unknown][]) =>
        set(
            prop.key,
            next.reduce<Record<string, unknown>>((acc, [k, v]) => {
                acc[k] = v;
                return acc;
            }, {})
        );
    return (
        <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium text-muted-foreground">{label}</label>
            <div className="space-y-1">
                {entries.map(([k, v], i) => (
                    <div key={i} className="flex items-center gap-1">
                        <Input
                            aria-label={`${label} key ${i + 1}`}
                            placeholder="key"
                            className="h-8! text-xs! flex-1"
                            value={k}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                writeEntries(entries.map((en, idx) => (idx === i ? [e.target.value, en[1]] : en)))
                            }
                        />
                        <Input
                            aria-label={`${label} value ${i + 1}`}
                            placeholder="value"
                            className="h-8! text-xs! flex-1"
                            value={typeof v === "string" ? v : String(v ?? "")}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                writeEntries(entries.map((en, idx) => (idx === i ? [en[0], e.target.value] : en)))
                            }
                        />
                        <button
                            type="button"
                            aria-label={`Remove ${label} ${i + 1}`}
                            onClick={() => writeEntries(entries.filter((_, idx) => idx !== i))}
                            className="p-1 text-muted-foreground hover:text-destructive"
                        >
                            <X className="w-3 h-3" />
                        </button>
                    </div>
                ))}
                <button
                    type="button"
                    onClick={() => writeEntries([...entries, ["", ""]])}
                    className="flex items-center gap-1 text-xs! text-primary hover:underline mt-1"
                >
                    <Plus className="w-3 h-3" /> Add {label.toLowerCase()}
                </button>
            </div>
            <Help text={prop.help} />
        </div>
    );
}
