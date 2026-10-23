import useGenerateWorkerMenu from "@/features/workflows/hooks/use-merge-nodes";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { chipItems, readRecents, recordRecent } from "@/features/workflows/stores/palette-recents";
import type { IMenuItem, INodePallete, IPaletteBand } from "@/features/workflows/types/node-pallete";
import { rankNodes } from "@/features/workflows/utils/rank-nodes";
import { useLanguage } from "@/hooks/use-language";
import { useEffect, useMemo, useState } from "react";
import PaletteChips from "./palette-chips";
import PaletteConnectorBand from "./palette-connector-band";
import PaletteCoreBand from "./palette-core-band";
import PaletteMineBand from "./palette-mine-band";
import PaletteNodeRow from "./palette-node-row";
import PaletteSearch from "./palette-search";

interface INodePaletteContentProps {
    onCloseDrawer?: () => void;
    onSelectNode: (node: INodePallete) => void;
}

/**
 * The expansion key. Core buckets are keyed by their MACHINE ID ("triggers", "flow-logic");
 * connectors and a named MINE collection by their COLLECTION NAME, which the BE takes
 * verbatim from whoever authored it (order 10 is given to ANY collection that is not Core /
 * "My Nodes"). A tenant with a collection called "triggers" would otherwise put one boolean
 * behind two rows — opening Core ▸ Triggers would open their collection with it. Same risk
 * between a connector and a business author's own collection sharing a name (SA-1930):
 * namespace all three key spaces.
 */
const ek = (scope: "core" | "col" | "mine", key: string) => `${scope}:${key}`;

const NodePaletteContent = ({ onCloseDrawer, onSelectNode }: INodePaletteContentProps) => {
    const { t } = useLanguage();

    const [searchString, setSearchString] = useState<string>("");
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [recents, setRecents] = useState<string[]>(() => readRecents());

    const { nodeMenuItems, bands, total, isLoading } = useGenerateWorkerMenu();
    const canvasNodes = useEditorStore((s) => s.workflowData.nodes);

    const collectionsInUse = useMemo(() => {
        const s = new Set<string>();
        for (const n of canvasNodes as any[]) {
            const cat = String(n?.data?.category ?? "");
            if (cat) {
                const i = cat.lastIndexOf(".");
                s.add(i >= 0 ? cat.slice(0, i) : cat);
            }
        }
        return s;
    }, [canvasNodes]);

    // A tenant with exactly one connector has nothing to choose between — open it.
    const connectors = bands.find((b) => b.id === "connector")?.collections ?? [];
    const onlyConnectorKey = connectors.length === 1 ? connectors[0]?.key : undefined;
    useEffect(() => {
        if (!onlyConnectorKey) return;
        const key = ek("col", onlyConnectorKey);
        setExpanded((prev) => (key in prev ? prev : { ...prev, [key]: true }));
    }, [onlyConnectorKey]);

    const add = (id: string) => setRecents(recordRecent(id));

    const select = (node: INodePallete) => {
        onSelectNode?.(node);
        onCloseDrawer?.();
    };

    const pickChip = (item: IMenuItem) => {
        add(item.original?.id ?? "");
        select(item.original!);
    };

    const chips = useMemo(() => chipItems(nodeMenuItems, recents), [nodeMenuItems, recents]);

    // Search (SA-1739) is unchanged: one flat ranked list, bands hidden.
    const results = useMemo(
        () => (searchString ? rankNodes(searchString, nodeMenuItems, { collectionsInUse }) : null),
        [searchString, nodeMenuItems, collectionsInUse],
    );

    const toggle = (key: string) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));

    const isExpanded = (scope: "core" | "col" | "mine") => (key: string) => !!expanded[ek(scope, key)];
    const onToggle = (scope: "core" | "col" | "mine") => (key: string) => toggle(ek(scope, key));

    /**
     * One layout per band. A band id with no layout renders NOTHING — and is a COMPILE
     * error, because it must never fall through to the connector layout, which is exactly
     * what the chained ternary this replaces did. The premise of this taxonomy is that a
     * new collection needs no code change; a new BAND is the one thing that does, so it has
     * to say so out loud instead of silently misrendering.
     */
    const renderBandBody = (band: IPaletteBand) => {
        const common = { onSelect: select, onAdd: add, onDragEnd: onCloseDrawer };

        switch (band.id) {
            case "core":
                return (
                    <PaletteCoreBand
                        collections={band.collections}
                        isExpanded={isExpanded("core")}
                        onToggle={onToggle("core")}
                        {...common}
                    />
                );
            case "connector":
                return (
                    <PaletteConnectorBand
                        collections={band.collections}
                        isExpanded={isExpanded("col")}
                        onToggle={onToggle("col")}
                        {...common}
                    />
                );
            case "mine":
                return (
                    <PaletteMineBand
                        collections={band.collections}
                        isExpanded={isExpanded("mine")}
                        onToggle={onToggle("mine")}
                        {...common}
                    />
                );
            default: {
                // Compile time: a new band id fails to type as `never` here, so adding one
                // without a layout does not build. Runtime (a band id from an older/newer
                // BE than this bundle): render NOTHING. `exhaustive` still holds the real
                // string at runtime — returning it would print the raw id at the user.
                const exhaustive: never = band.id;
                void exhaustive;
                return null;
            }
        }
    };

    return (
        <div className="flex flex-col gap-3 h-full overflow-hidden py-3">
            <PaletteSearch total={total} onChange={setSearchString} />

            {!searchString && <PaletteChips items={chips} onPick={pickChip} />}

            <div className="flex-2 overflow-y-auto overflow-x-hidden py-1">
                {isLoading && (
                    <div className="flex flex-col px-4 gap-6">
                        {Array.from({ length: 6 }).map((_, i) => (
                            <div key={i} className="flex items-center gap-1">
                                <div className="w-8 h-8 bg-muted rounded animate-pulse" />
                                <div className="w-full h-8 bg-muted rounded animate-pulse" />
                            </div>
                        ))}
                    </div>
                )}

                {/* A search that matches nothing must SAY so: the bands are hidden by then, so
                    otherwise the user is left looking at an empty panel that reads as broken. */}
                {results?.length === 0 && (
                    <p className="px-4 py-6 text-sm text-muted-foreground text-center">
                        {t("palette_no_results", { query: searchString })}
                    </p>
                )}

                {results
                    ? results.map((node) => (
                          <PaletteNodeRow
                              key={node.original?.id ?? node.text}
                              node={node}
                              onSelect={select}
                              onAdd={add}
                              onDragEnd={onCloseDrawer}
                          />
                      ))
                    : bands.map((band) => {
                          const body = renderBandBody(band);
                          if (!body) return null;

                          return (
                              <div key={band.id}>
                                  <h4 className="px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                                      {t(`palette_band_${band.id}`)}
                                  </h4>
                                  {body}
                              </div>
                          );
                      })}
            </div>
        </div>
    );
};

export default NodePaletteContent;
