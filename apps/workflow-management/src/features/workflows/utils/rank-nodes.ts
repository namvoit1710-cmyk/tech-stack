import type { IMenuItem } from "@/features/workflows/types/node-pallete";
import { collectionOf } from "@/features/workflows/utils/palette-taxonomy";

/** Leading verb of a node name, lowercased ("Create Issue" -> "create"). */
function leadingVerb(item: IMenuItem): string {
    return (item.text ?? "").trim().split(/\s+/)[0]?.toLowerCase() ?? "";
}

/**
 * Rank palette search results (SA-1739). REORDERS the substring-matched set only —
 * it does not change which items match. Order: verb-match (leading verb startsWith
 * query) -> connector-in-use (item's collection is on the canvas) -> original order.
 * Recency is intentionally out of scope (E-P2).
 */
export function rankNodes(
    query: string,
    items: IMenuItem[],
    opts: { collectionsInUse: Set<string> } = { collectionsInUse: new Set() },
): IMenuItem[] {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    const matched = items.filter(
        (n) => n.text.toLowerCase().includes(q) || (n.original?.description ?? "").toLowerCase().includes(q),
    );
    return matched
        .map((n, i) => ({
            n,
            i,
            verb: leadingVerb(n).startsWith(q) ? 0 : 1,
            inUse: opts.collectionsInUse.has(collectionOf(n)) ? 0 : 1,
        }))
        .sort((a, b) => a.verb - b.verb || a.inUse - b.inUse || a.i - b.i)
        .map((x) => x.n);
}
