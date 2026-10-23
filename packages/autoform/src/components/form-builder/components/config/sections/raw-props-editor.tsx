import type React from "react";
import { useEffect, useRef, useState } from "react";

import {
    Autocomplete,
    AutocompleteContent,
    AutocompleteInput,
    AutocompleteItem,
    AutocompleteList,
} from "@ldc/ui/blocks/autocomplete/autocomplete";
import { Input } from "@ldc/ui/components/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

import LdcCodeEditor from "../../../../field-control/code-control";
import { COMPACT_FIELD_CLASS, ItemCard, ItemList, SECTION_ROWS_PADDING_CLASS } from "../shared/item-card";
import type { WpType } from "../shared/prop-value-utils";
import { WP_TYPES, coerceValue, inferType } from "../shared/prop-value-utils";

/**
 * One editable row.
 *
 * `id` is what React keys on, deliberately NOT the prop key: keying on the key means a rename
 * changes the row's identity, React remounts it, and the input the user is typing into is
 * destroyed mid-word. Mirrors `useFieldArray`'s `field.id` — which is why that grid is typeable.
 *
 * `type` is the USER's choice, held per row — only ever INITIALISED from `inferType`, never
 * re-derived afterwards. Re-inferring on each render flips a `json` row to `string` the moment
 * its text is mid-keystroke and unparseable, unmounting Monaco under the cursor.
 *
 * `value` is always the STRING on screen; coercion happens once, on flush.
 */
interface RawEntry {
    id: string;
    key: string;
    value: string;
    type: WpType;
}

/** Row ids only need to be unique within a mounted list — a counter is enough. */
let rowSeq = 0;
const nextRowId = () => `raw-${++rowSeq}`;

/** Stable identity for the default `autoJsonKeys` — never a fresh array per render. */
const EMPTY_KEYS: string[] = [];

/**
 * Stringify a raw value for the row's editor. Objects render as PRETTY JSON, matching the
 * inline grid (`toWpEntries` / `toControlPropEntries` indent by 2 before Monaco sees them).
 *
 * Deliberately NOT `shared/prop-value-utils`'s `stringifyValue`: that one renders objects
 * COMPACT because it feeds single-line inputs, so merging the two would change what those
 * render. `inferType` and `coerceValue` ARE identical, so those are imported, not re-declared.
 */
function stringifyRowValue(v: unknown): string {
    if (v === null || v === undefined) return "";
    if (typeof v === "object") return JSON.stringify(v, null, 2);
    if (typeof v === "string") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return JSON.stringify(v) ?? "";
}

/** Seed the row list from the document, dropping the keys the typed form already owns. */
function seedEntries(value: Record<string, unknown>, coveredKeys: string[], autoJsonKeys: string[]): RawEntry[] {
    return Object.entries(value)
        .filter(([k]) => !coveredKeys.includes(k))
        .map(([k, v]) => ({
            id: nextRowId(),
            key: k,
            // An auto-json key is json even if the document currently holds a string there
            // (a legacy `actions: "[...]"`), so the row opens in the code editor and the
            // text is re-parsed on the first flush.
            type: autoJsonKeys.includes(k) ? "json" : inferType(v),
            value: stringifyRowValue(v),
        }));
}

export interface RawPropsEditorProps {
    /** The selected node's id. Changing it — and ONLY it — re-seeds the rows. */
    nodeId: string;
    value: Record<string, unknown>;
    /** Keys already handled by the typed form — hidden here so they are not edited twice. */
    coveredKeys: string[];
    /** Autocomplete hints for new keys. */
    suggestions: string[];
    /**
     * Keys that are ALWAYS json (OData: `["actions"]`). Such a row is PINNED: it renders a
     * static `json` label instead of a type Select, so the user cannot flip the one prop
     * production tables genuinely depend on to `string` and corrupt it. Renaming a row INTO
     * one of these keys switches it to json. Carried over from the container grid this
     * replaces (its `autoJsonKeys`).
     */
    autoJsonKeys?: string[];
    onChange: (next: Record<string, unknown>) => void;
}

/**
 * The Advanced escape hatch: the key/value/type grid, filtered to the keys the typed schema
 * does NOT cover. This is what makes dropping a prop from a schema safe — removing a key from
 * the schema removes it from the CURATED UI, never from the DOCUMENT: it stops being
 * "covered" and reappears here, still visible, still editable, still saved. That is how
 * `actions` (a large JSON DSL real OData tables depend on and no typed field models) and
 * legacy props (`endpoint`, `isShowRowMoreAction`, …) survive the cleanup untouched.
 *
 * The rows are held in LOCAL state, not derived from `value` on every render — the same shape
 * `useFieldArray` gives `sections/props-grid.tsx`. A row therefore has a life of its own: it
 * can be blank, half-renamed, and its declared type can disagree with what its text currently
 * parses to, all without the document being rewritten underneath the user's cursor.
 */
export function RawPropsEditor({
    nodeId,
    value,
    coveredKeys,
    suggestions,
    autoJsonKeys = EMPTY_KEYS,
    onChange,
}: RawPropsEditorProps) {
    const [entries, setEntries] = useState<RawEntry[]>(() => seedEntries(value, coveredKeys, autoJsonKeys));

    // Re-seed ONLY when a different node is selected. Re-seeding on every `value` change
    // would throw away the local row state (blank rows, sticky types) on each keystroke —
    // it is the write we just made coming back to overwrite the rows that made it.
    const seededFor = useRef(nodeId);
    useEffect(() => {
        if (seededFor.current === nodeId) return; // already seeded for this node (incl. mount)
        seededFor.current = nodeId;
        setEntries(seedEntries(value, coveredKeys, autoJsonKeys));
        // `nodeId` is the ONLY re-seed trigger — `value`/`coveredKeys` are deliberately not
        // deps. Same dependency list the field form uses (`[node._id]`).
    }, [nodeId]);

    /**
     * The type the row is ACTUALLY read and rendered as. A pinned key is json no matter what
     * the row's own `type` says — the pin is a guarantee, not a default the user can drift
     * away from. Used for BOTH the widget and the flush coercion, so what the editor shows
     * and what the document stores can never disagree.
     */
    const typeOf = (entry: RawEntry): WpType =>
        autoJsonKeys.includes(entry.key.trim()) ? "json" : entry.type;

    /**
     * Rebuild the document from the rows and hand the WHOLE object up (the same contract
     * `PropSchemaFields` has). Rows with a blank key are DROPPED, not written: a blank row is
     * a UI affordance the user is still filling in, and persisting it would put a literal ""
     * key into `wrapperProps`.
     *
     * Covered keys are copied through FIRST, so the typed form's half survives — rebuilding
     * from the rows alone would delete every typed prop the moment a raw row was touched.
     * Rows are written SECOND, so on a collision the ROW WINS (the rule of the grid this
     * replaces: what is in the grid is what the document gets). Not reachable by accident —
     * covered keys are filtered out on seed and never suggested, so it must be typed by hand.
     */
    const flush = (next: RawEntry[]) => {
        const out: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(value)) {
            if (coveredKeys.includes(k)) out[k] = v;
        }
        for (const entry of next) {
            const key = entry.key.trim();
            if (key) out[key] = coerceValue(typeOf(entry), entry.value);
        }
        onChange(out);
    };

    /** Apply a patch to one row, then flush. */
    const commit = (id: string, patch: Partial<RawEntry>) => {
        const next = entries.map((e) => (e.id === id ? { ...e, ...patch } : e));
        setEntries(next);
        flush(next);
    };

    /**
     * Renaming a row INTO a pinned key makes it json — the same `setValue(..., "json")` the
     * original grid fires on rename. `typeOf` forces it anyway; writing it into the row's own
     * state keeps the state honest, not just the render.
     */
    const rename = (id: string, key: string) =>
        commit(id, autoJsonKeys.includes(key.trim()) ? { key, type: "json" } : { key });

    const remove = (id: string) => {
        const next = entries.filter((e) => e.id !== id);
        setEntries(next);
        flush(next);
    };

    // Local only: a blank row has no key yet, so there is nothing to write — it reaches the
    // document on the first keystroke that gives it one.
    const add = () => setEntries((prev) => [...prev, { id: nextRowId(), key: "", value: "", type: "string" }]);

    return (
        <div className={`mb-1 ${SECTION_ROWS_PADDING_CLASS}`}>
            <p className="text-[10px] font-medium text-muted-foreground mb-1.5">
                Raw props (<span data-testid="raw-props-count">{entries.length}</span>)
            </p>
            <ItemList isEmpty={entries.length === 0} emptyLabel="No raw props yet" addLabel="Add" onAdd={add}>
                {entries.map((entry) => {
                    const type = typeOf(entry);
                    const isAutoJson = autoJsonKeys.includes(entry.key.trim());
                    return (
                        <ItemCard
                            key={entry.id}
                            removeLabel="Remove"
                            onRemove={() => remove(entry.id)}
                            header={
                                <>
                                    <Autocomplete
                                        items={suggestions}
                                        value={entry.key}
                                        onValueChange={(next: string) => rename(entry.id, next)}
                                    >
                                        <AutocompleteInput
                                            placeholder="key"
                                            className={`${COMPACT_FIELD_CLASS} flex-1 min-w-0`}
                                        />
                                        <AutocompleteContent>
                                            <AutocompleteList>
                                                {(item: string) => (
                                                    <AutocompleteItem key={item} value={item}>
                                                        {item}
                                                    </AutocompleteItem>
                                                )}
                                            </AutocompleteList>
                                        </AutocompleteContent>
                                    </Autocomplete>

                                    {isAutoJson ? (
                                        // Pinned: a static label, not a Select — there is no other legal
                                        // type for this key, so the panel offers no way to pick one.
                                        <div
                                            data-testid={`raw-prop-type-${entry.key.trim()}`}
                                            className="h-8 w-[72px] shrink-0 px-1.5 flex items-center justify-center rounded-md bg-muted/50 text-[10px] text-muted-foreground"
                                        >
                                            json
                                        </div>
                                    ) : (
                                        <Select
                                            value={entry.type}
                                            // Re-coerces on flush: the text on screen is untouched, but it
                                            // is now read as the new type (`"1"` → the number 1).
                                            onValueChange={(t: string) => commit(entry.id, { type: t as WpType })}
                                        >
                                            <SelectTrigger aria-label="Type" size="sm" className="text-[10px] w-[72px] shrink-0 px-1.5">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {WP_TYPES.map((t) => (
                                                    <SelectItem key={t} value={t} className="text-xs!">
                                                        {t}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    )}
                                </>
                            }
                        >
                            {type === "json" ? (
                                <LdcCodeEditor
                                    language="json"
                                    value={entry.value}
                                    onChange={(v: string) => commit(entry.id, { value: v })}
                                    minHeight={80}
                                    maxHeight={240}
                                />
                            ) : (
                                <Input
                                    placeholder={type === "boolean" ? "true / false" : "value"}
                                    className={`${COMPACT_FIELD_CLASS} w-full`}
                                    value={entry.value}
                                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                        commit(entry.id, { value: e.target.value })
                                    }
                                />
                            )}
                        </ItemCard>
                    );
                })}
            </ItemList>
        </div>
    );
}

export default RawPropsEditor;
