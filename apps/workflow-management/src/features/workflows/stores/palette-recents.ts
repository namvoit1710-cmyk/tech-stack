import type { IMenuItem } from "@/features/workflows/types/node-pallete";
import { CORE_COLLECTION, MINE_COLLECTION, collectionOf, kindOf } from "@/features/workflows/utils/palette-taxonomy";

export const RECENTS_KEY = "smdg.palette.recents";
export const RECENTS_CAP = 8;
export const CHIP_COUNT = 3;

function load(): string[] {
    try {
        const raw = window.localStorage.getItem(RECENTS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
        // corrupt JSON, or storage blocked (private mode) — recents are a nicety, never a failure.
        return [];
    }
}

export function readRecents(): string[] {
    return load();
}

/**
 * Most-recent-first, deduped, capped. Called on click-add AND on drag-start.
 * A drag the user cancels still counts: the palette cannot observe the drop.
 * Stated rather than hidden.
 */
export function recordRecent(id: string): string[] {
    if (!id) return load();
    const next = [id, ...load().filter((x) => x !== id)].slice(0, RECENTS_CAP);
    try {
        window.localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
    } catch {
        /* storage blocked — keep the in-memory order for this session */
    }
    return next;
}

/**
 * What a user with no history should see: the first write and the first read of
 * the connector they have most of, then a Core branch. All data-driven — no node
 * id is ever hardcoded.
 */
export function suggestChips(items: IMenuItem[]): IMenuItem[] {
    const byCollection = new Map<string, IMenuItem[]>();
    for (const item of items) {
        const c = collectionOf(item);
        if (!c || c === CORE_COLLECTION || c === MINE_COLLECTION) continue;
        byCollection.set(c, [...(byCollection.get(c) ?? []), item]);
    }

    let biggest: IMenuItem[] = [];
    for (const list of byCollection.values()) {
        if (list.length > biggest.length) biggest = list;
    }

    const out: IMenuItem[] = [];
    const firstOfKind = (pool: IMenuItem[], kind: string) => pool.find((i) => kindOf(i.original) === kind);

    const action = firstOfKind(biggest, "action");
    if (action) out.push(action);
    const read = firstOfKind(biggest, "read");
    if (read) out.push(read);
    const core = items.filter((i) => collectionOf(i) === CORE_COLLECTION);
    const logic = firstOfKind(core, "logic");
    if (logic) out.push(logic);

    return out;
}

/** The chips to render: recents resolved against the live catalog, padded with suggestions. */
export function chipItems(items: IMenuItem[], recents: string[]): IMenuItem[] {
    const byId = new Map(items.map((i) => [i.original?.id ?? "", i]));
    const picked: IMenuItem[] = [];
    const taken = new Set<string>();

    for (const id of recents) {
        const item = byId.get(id);
        if (!item || taken.has(id)) continue;
        picked.push(item);
        taken.add(id);
        if (picked.length >= CHIP_COUNT) return picked;
    }

    for (const item of suggestChips(items)) {
        const id = item.original?.id ?? "";
        if (taken.has(id)) continue;
        picked.push(item);
        taken.add(id);
        if (picked.length >= CHIP_COUNT) break;
    }

    return picked.slice(0, CHIP_COUNT);
}
