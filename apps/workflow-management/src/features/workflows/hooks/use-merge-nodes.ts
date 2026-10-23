import { useQueryNodeCatalog } from "@/features/workflows/hooks/apis/node-pallete";
import type { IMenuItem, IPaletteBand, IPaletteCollection, IPaletteSection, NodeProvider, PaletteBandId } from "@/features/workflows/types/node-pallete";
import { CORE_BUCKETS, bandOfCollection, kindLabelKey, previewNames, vendorOf } from "@/features/workflows/utils/palette-taxonomy";
import { useMemo } from "react";

const BAND_ORDER: PaletteBandId[] = ["core", "connector", "mine"];

const useGenerateWorkerMenu = () => {
    const { data: nodes, isLoading: isNodeLoading } = useQueryNodeCatalog();

    const nodeMenuItems = useMemo<IMenuItem[]>(() => {
        return (nodes?.data?.definitions ?? []).map((definition) => ({
            text: definition.name,
            type: "Node" as const,
            icon: definition.icon ?? "ethernet-port",
            original: definition,
        }));
    }, [nodes]);

    const bands = useMemo<IPaletteBand[]>(() => {
        const cats = nodes?.data?.categories ?? {};
        const tree = nodes?.data?.category_tree ?? {};
        const orderOf = (key: string) => cats[key]?.order ?? Number.MAX_SAFE_INTEGER;
        const labelOf = (key: string, fallback: string) => cats[key]?.label ?? fallback;
        const leavesFor = (leafKey: string) => nodeMenuItems.filter((n) => n.original?.category === leafKey);

        // Group each leaf category key ("<collection>.<kind>") by its collection prefix.
        // Split at the LAST "." — a collection never contains one (the BE sanitises it).
        const byCollection = new Map<string, string[]>();
        for (const leafKey of Object.keys(tree)) {
            const i = leafKey.lastIndexOf(".");
            const collection = i >= 0 ? leafKey.slice(0, i) : leafKey;
            byCollection.set(collection, [...(byCollection.get(collection) ?? []), leafKey]);
        }

        const built: Array<{ band: PaletteBandId; order: number; collection: IPaletteCollection }> = [];

        for (const [collection, leafKeys] of byCollection) {
            const order = orderOf(collection);

            const rawSections = leafKeys
                .slice()
                .sort((a, b) => orderOf(a) - orderOf(b))
                .map((leafKey) => {
                    const i = leafKey.lastIndexOf(".");
                    const kind = i >= 0 ? leafKey.slice(i + 1) : "";
                    const items = leavesFor(leafKey);
                    return {
                        key: leafKey,
                        // The BE's label is the FALLBACK, not the source: its _KIND_LABEL map is
                        // English-only, so a connector's headers would ship English to a jp user.
                        // A kind we have no key for (one the BE adds tomorrow) keeps the BE label.
                        label: labelOf(leafKey, kind || collection),
                        labelKey: kindLabelKey(kind),
                        kind,
                        count: items.length,
                        items,
                    };
                });

            // SA-1930: the band is decided by what the collection's nodes ARE (`provider`),
            // not merely by the order the BE gave it. get_category_info returns order 10 for
            // ANY bare key that is not "Core"/"My Nodes" — including a business author's own
            // collection ("Finance") — so `bandOf(order)` alone used to file it under APPS &
            // CONNECTORS as if it were a third-party connector.
            const providers = new Set<NodeProvider>();
            for (const s of rawSections) {
                for (const i of s.items) {
                    if (i.original?.provider) providers.add(i.original.provider);
                }
            }
            const band = bandOfCollection(providers, order);

            // A CONNECTOR is a collection that actually ships worker nodes — equivalent to
            // `band === "connector"` (bandOfCollection's own worker-wins priority), kept as
            // its own flag because it also gates the vendor subtitle below.
            const isConnector = band === "connector";

            const kindSections: IPaletteSection[] = rawSections;

            let sections = kindSections;

            if (band === "core") {
                // Core is browsed by function, so its kind sections are refolded into buckets.
                // Widened to Set<string>/string[] for the membership test only: `s.kind` is a
                // section's kind (IPaletteSection.kind: string — see its doc comment), which
                // is NOT guaranteed to be a NodeKind (a kind the BE adds tomorrow, or "" from a
                // flat key). Checking "is this arbitrary string one of the known ones" is an
                // honest containment test, not a claim that it IS one — so the array/set, not
                // the value, is what gets widened.
                const claimed: Set<string> = new Set(CORE_BUCKETS.flatMap((b) => b.kinds));
                const buckets = CORE_BUCKETS.map((bucket) => {
                    const bucketKinds: string[] = bucket.kinds;
                    const items = kindSections
                        .filter((s) => bucketKinds.includes(s.kind))
                        .flatMap((s) => s.items);
                    return {
                        key: bucket.id,
                        label: bucket.label,
                        labelKey: bucket.labelKey,
                        // The bucket's REPRESENTATIVE kind — used only to pick its chrome colour.
                        kind: bucket.kinds[0] ?? "",
                        icon: bucket.icon,
                        count: items.length,
                        items,
                        preview: previewNames(items),
                    };
                }).filter((s) => s.count > 0);

                // Never-Worse: a kind no bucket claims (a kind the BE adds tomorrow, or "" from
                // a legacy flat key) keeps its raw section rather than vanishing. A node must
                // never be lost to regrouping — and `count` below is summed from what we
                // actually render, so the header can never claim a node the user cannot reach.
                const orphans = kindSections.filter((s) => !claimed.has(s.kind) && s.count > 0);
                sections = [...buckets, ...orphans];
            }

            // Summed from `sections` — the array that is RENDERED — not from `kindSections`,
            // which Core replaces. A count taken from the other array is a header that lies.
            const count = sections.reduce((sum, s) => sum + s.count, 0);

            built.push({
                band,
                order,
                collection: {
                    key: collection,
                    label: labelOf(collection, collection),
                    // Gated on isConnector (== band === "connector", SA-1930): a business
                    // author's own collection never lands here unless it also ships a worker
                    // node, so `vendorOf` is only ever consulted for a real connector shelf.
                    vendor: isConnector ? vendorOf(collection) : undefined,
                    icon: kindSections.flatMap((s) => s.items)[0]?.icon,
                    count,
                    sections,
                },
            });
        }

        return BAND_ORDER.map((id) => ({
            id,
            collections: built
                .filter((b) => b.band === id)
                .sort((a, b) => a.order - b.order || a.collection.label.localeCompare(b.collection.label))
                .map((b) => b.collection),
        })).filter((b) => b.collections.length > 0);
    }, [nodes, nodeMenuItems]);

    return {
        nodeMenuItems,
        bands,
        total: nodes?.data?.total ?? nodeMenuItems.length,
        isLoading: isNodeLoading,
    };
};

export default useGenerateWorkerMenu;
