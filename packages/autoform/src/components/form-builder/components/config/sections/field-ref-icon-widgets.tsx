import type React from "react";
import { DynamicIcon } from "lucide-react/dynamic";
import type { IconName } from "lucide-react/dynamic";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

import { Help, NOT_SET, type WidgetProps } from "./prop-widgets";

/**
 * `field-ref` (SA-2040) — a Select over sibling field keys (a closed set) with a "Not set" that
 * DELETES the key (Radix Select forbids an empty-string value — same contract as EnumWidget).
 * Consumed by Code `languageField` and Button `watchField`. The keys arrive via `fieldRefOptions`,
 * threaded from `PropSchemaFields` (the only widget that reads it). Split into its own file to keep
 * `prop-widgets.tsx` under the config-folder 300-line guard (`config-panel.size.test.ts`), exactly
 * as `DateTimeValueWidget` was.
 */
export function FieldRefWidget({ prop, current, id, label, set, unset, fieldRefOptions }: WidgetProps) {
    const options = fieldRefOptions ?? [];
    return (
        <div className="flex flex-col gap-1">
            <label htmlFor={id} className="text-[10px] font-medium text-muted-foreground">
                {label}
            </label>
            <Select
                value={typeof current === "string" && current ? current : NOT_SET}
                onValueChange={(v: string) => (v === NOT_SET ? unset(prop.key) : set(prop.key, v))}
            >
                <SelectTrigger id={id} aria-label={label} className="h-8! w-full text-xs!">
                    <SelectValue placeholder="Select a field" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value={NOT_SET} className="text-xs!">
                        Not set
                    </SelectItem>
                    {options.map((k) => (
                        <SelectItem key={k} value={k} className="text-xs!">
                            {k}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
            <Help text={prop.help} />
        </div>
    );
}

/** A short, curated autocomplete list; any lucide name is still accepted free-form. */
const ICON_SUGGESTIONS = [
    "plus", "pencil", "trash-2", "download", "upload", "check", "x", "search",
    "filter", "save", "copy", "eye", "settings", "refresh-cw", "arrow-right",
    "chevron-right", "file", "folder", "star", "play",
];

/** Mirror button-control.tsx: DynamicIcon wants kebab-case (`IconName`). */
function toKebabIcon(str: string): string {
    return str
        .replace(/([a-z])([A-Z])/g, "$1-$2")
        .replace(/([A-Z]+)([A-Z][a-z])/g, "$1-$2")
        .toLowerCase();
}

/**
 * `icon` (SA-2040) — a typeable lucide-name Input with a live DynamicIcon preview and a `<datalist>`
 * of common names. Stores the raw name string; an invalid name simply previews nothing (kebab-cased
 * to match how `button-control.tsx` renders it). Clearing DELETES the key.
 */
export function IconWidget({ prop, current, id, label, set, unset }: WidgetProps) {
    const iconName = typeof current === "string" ? current : "";
    return (
        <div className="flex flex-col gap-1">
            <label htmlFor={id} className="text-[10px] font-medium text-muted-foreground">
                {label}
            </label>
            <div className="flex items-center gap-2">
                <span
                    aria-hidden
                    className="flex size-8 shrink-0 items-center justify-center rounded-md border border-input"
                >
                    {iconName ? (
                        <DynamicIcon name={toKebabIcon(iconName) as IconName} className="size-4" />
                    ) : null}
                </span>
                <Input
                    id={id}
                    aria-label={label}
                    list={`${id}-icons`}
                    className="h-8! text-xs!"
                    placeholder="lucide icon, e.g. pencil"
                    value={iconName}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                        const raw = e.target.value;
                        if (raw.trim() === "") unset(prop.key);
                        else set(prop.key, raw);
                    }}
                />
                <datalist id={`${id}-icons`}>
                    {ICON_SUGGESTIONS.map((n) => (
                        <option key={n} value={n} />
                    ))}
                </datalist>
            </div>
            <Help text={prop.help} />
        </div>
    );
}
