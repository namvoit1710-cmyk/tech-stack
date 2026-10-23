import type { IMenuItem, INodePallete, NodeKind, NodeProvider, PaletteBandId } from "@/features/workflows/types/node-pallete";

export const CORE_COLLECTION = "Core";
export const MINE_COLLECTION = "My Nodes";

/**
 * Origin band of a collection, read off the `order` the BE already returns
 * (category_metadata.get_category_info: Core=0, connector=10, "My Nodes"=90).
 * No BE field is added — a new connector lands in the connector band for free.
 */
export function bandOf(order: number): PaletteBandId {
    if (order <= 0) return "core";
    if (order >= 90) return "mine";
    return "connector";
}

/**
 * SA-1930: which band a collection belongs to, decided by what its nodes ARE (`provider`),
 * not by the name the BE happened to give it. The BE assigns order 10 to ANY collection that
 * is not "Core"/"My Nodes" — including a business author's own — which used to file a user's
 * personal collection under APPS & CONNECTORS as if it were a third-party connector.
 *
 * Priority: builtin > worker > business.
 *
 * builtin FIRST — this is a BLOCKER fix, not stylistic. The node-definition builder's
 * Collection field is free text, and the BE's `_sanitize_collection` only strips ".";
 * there is no reserved-name guard. So a user can create a business node with
 * `collection = "Core"`, giving the real "Core" collection `providers = {builtin,
 * business}`. If `business` won that check (as it used to), the ENTIRE "Core" collection —
 * all 18 builtins, not just the offending node — would be filed under "mine", leaving the
 * core band with zero collections. `useGenerateWorkerMenu`'s `.filter((b) =>
 * b.collections.length > 0)` then drops the whole core band, and Trigger/Condition/Loop/
 * Switch/Merge/Parallel/Output/Compute all render under MY NODES. No privilege required —
 * one free-text field. builtin must win so a hijacked "Core" collection stays core.
 *
 * worker before business: a collection holding real worker nodes is still a connector shelf
 * even if a user also filed a node of their own into it (naming their collection "Jira" is a
 * deliberate merge — collection is the browse axis, per the SA-1734 spec).
 *
 * `order` is the FALLBACK only: a collection whose provider we do not recognise keeps its
 * old placement rather than disappearing (Never-Worse).
 */
export function bandOfCollection(providers: Set<NodeProvider>, order: number): PaletteBandId {
    if (providers.has("builtin")) return "core";
    if (providers.has("worker")) return "connector";
    if (providers.has("business")) return "mine";
    return bandOf(order);
}

export interface ICoreBucket {
    id: string;
    /** Human fallback copy. `labelKey` wins in the component; this is what a consumer
     *  that falls back to `label` renders — never the machine id ("flow-logic"). */
    label: string;
    labelKey: string;
    icon: string;
    kinds: NodeKind[];
}

/**
 * kind -> i18n key for a KIND SECTION's header.
 *
 * A connector's sections take their label from the BE's `categories`, and the BE's
 * _KIND_LABEL map is English-only ("Actions", "Find & Read", "Utilities"). Rendering it
 * verbatim ships English headers to a jp user, sitting right next to correctly-translated
 * Core buckets. So the label is resolved here instead — the BE's label survives only as
 * the fallback for a kind we have no translation for (see `kindLabelKey`).
 *
 * The English copy of these keys MUST match what the BE already says, so an `en` user
 * sees no change.
 */
export const KIND_LABEL_KEY = {
    trigger: "palette_kind_trigger",
    action: "palette_kind_action",
    read: "palette_kind_read",
    logic: "palette_kind_logic",
    human: "palette_kind_human",
    transform: "palette_kind_transform",
    util: "palette_kind_util",
} as const satisfies Record<NodeKind, string>;

/**
 * The i18n key for a TRIGGERS section — ONE key, not two. The Core "Triggers" bucket, a
 * connector's TRIGGERS section, and the empty-trigger section the hook synthesises for a
 * connector with no triggers all name the same thing, so they all resolve the same key.
 * (The synthesised one has no BE category entry to read a label from at all — the BE only
 * expands keys present in the tree — so for it the key is the only path, not a nicety.)
 */
export const TRIGGERS_LABEL_KEY: string = KIND_LABEL_KEY.trigger;

/**
 * The i18n key for a kind's section header, or `undefined` for a kind we do not know.
 * Undefined is deliberate: a kind the BE adds tomorrow has no translation, and inventing
 * a key would render the raw key string at the user. The caller falls back to the BE's
 * label, which is at least true.
 */
export function kindLabelKey(kind: string): string | undefined {
    return (KIND_LABEL_KEY as Record<string, string>)[kind];
}

/**
 * Core is browsed by function, not by kind name. The buckets must PARTITION the
 * kind vocabulary — a kind in no bucket is a Core node that disappears from the
 * palette. `read` has no Core member today; it is bucketed anyway so a future
 * read-ish builtin cannot vanish.
 *
 * Even so, the hook must NOT assume this list is total: a kind the BE adds tomorrow
 * (or a flat key with kind "") is claimed by no bucket, and is kept as its own section.
 */
export const CORE_BUCKETS: ICoreBucket[] = [
    { id: "triggers", label: "Triggers", labelKey: TRIGGERS_LABEL_KEY, icon: "zap", kinds: ["trigger"] },
    { id: "flow-logic", label: "Flow & Logic", labelKey: "palette_bucket_flow_logic", icon: "git-branch", kinds: ["logic"] },
    { id: "approvals-forms", label: "Approvals & Forms", labelKey: "palette_bucket_approvals", icon: "square-pen", kinds: ["human"] },
    { id: "data-files", label: "Data & Files", labelKey: "palette_bucket_data_files", icon: "shuffle", kinds: ["transform", "util", "action", "read"] },
];

export interface IKindTint {
    text: string;
    bg: string;
    rule: string;
}

/**
 * kind -> colour, for GROUP CHROME ONLY: a kind section's left rule, a Core
 * bucket row's icon tile. NEVER for a leaf.
 *
 * A node already HAS a colour, and it is real data (verified 2026-07-13):
 *   - Core builtins each pick their own in their NodeSpec — Trigger #F59E0B,
 *     Loop #F97316, Condition #8B5CF6, Merge/Output #10B981, Compute #64748B …
 *   - every jira-worker node carries settings.WORKER_COLOR = #0052CC (Atlassian blue)
 *   - a business node carries the colour its author picked in the builder's
 *     ColorPicker (the same 16-swatch palette the specs draw from)
 * Repainting a leaf by kind would throw all of that away — and would silently
 * undo the colour field we shipped in SA-1736 (sub-task B).
 *
 * Groups have no colour of their own (they span many nodes), so a group is exactly
 * where `kind` is free to speak. And what actually makes "9 of these only read data"
 * visible is the labelled section + its count — not repainting every row.
 */
export const KIND_TINT: Record<NodeKind, IKindTint> = {
    trigger: { text: "text-amber-600", bg: "bg-amber-50", rule: "bg-amber-400" },
    action: { text: "text-green-600", bg: "bg-green-50", rule: "bg-green-500" },
    read: { text: "text-blue-600", bg: "bg-blue-50", rule: "bg-blue-500" },
    logic: { text: "text-violet-600", bg: "bg-violet-50", rule: "bg-violet-500" },
    human: { text: "text-rose-600", bg: "bg-rose-50", rule: "bg-rose-500" },
    transform: { text: "text-cyan-600", bg: "bg-cyan-50", rule: "bg-cyan-500" },
    util: { text: "text-slate-500", bg: "bg-slate-100", rule: "bg-slate-400" },
};

export const DEFAULT_TINT: IKindTint = {
    text: "text-muted-foreground",
    bg: "bg-muted",
    rule: "bg-muted-foreground",
};

export const tintOf = (kind: string): IKindTint => (KIND_TINT as Record<string, IKindTint>)[kind] ?? DEFAULT_TINT;

/**
 * Connector subtitle copy. Presentation only — the BE has no vendor field, and
 * SA-1930 adds none. Hold ONLY entries we can vouch for; an absent vendor just
 * means the subtitle is "{n} operations".
 */
export const VENDOR_BLURB: Record<string, string> = {
    Jira: "Atlassian",
};

export const vendorOf = (collection: string): string | undefined => VENDOR_BLURB[collection];

/** "Jira: Create Issue" shown inside the Jira group -> "Create Issue". */
export function stripCollectionPrefix(name: string, collection: string): string {
    const prefix = `${collection}:`;
    if (name.toLowerCase().startsWith(prefix.toLowerCase())) {
        return name.slice(prefix.length).trim();
    }
    return name;
}

/** "Condition · Loop · Merge · Switch" — what is inside a group, without opening it. */
export function previewNames(items: IMenuItem[], max = 4): string {
    return items.slice(0, max).map((i) => i.text).join(" · ");
}

/** The node's kind, from the taxonomy the BE sends; falls back to the category suffix. */
export function kindOf(node?: INodePallete): string {
    if (!node) return "";
    if (node.kind) return node.kind;
    const category = node.category ?? "";
    const i = category.lastIndexOf(".");
    return i >= 0 ? category.slice(i + 1) : "";
}

/** The node's collection, from the taxonomy the BE sends; falls back to the category prefix. */
export function collectionOf(item: { original?: INodePallete }): string {
    const c = item.original?.collection;
    if (c) return c;
    const category = item.original?.category ?? "";
    const i = category.lastIndexOf(".");
    return i >= 0 ? category.slice(0, i) : category;
}

/**
 * A leaf's tile colours — straight from the BE, exactly as the palette does today.
 * Extracted so the invariant "the palette never repaints a node" is unit-testable.
 */
export function nodeTileStyle(node?: INodePallete): { color?: string; backgroundColor?: string } {
    const color = node?.color;
    if (!color) return {};
    return { color, backgroundColor: `color-mix(in srgb, ${color} 10%, transparent)` };
}
