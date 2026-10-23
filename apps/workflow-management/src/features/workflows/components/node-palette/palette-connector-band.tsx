import type { INodePallete, IPaletteCollection } from "@/features/workflows/types/node-pallete";
import { useLanguage } from "@/hooks/use-language";
import PaletteCollectionRow from "./palette-collection-row";
import PaletteKindSection from "./palette-kind-section";

interface IPaletteConnectorBandProps {
    collections: IPaletteCollection[];
    /** Scoped by the parent: a collection is named by the BE and can collide with a bucket id. */
    isExpanded: (key: string) => boolean;
    onToggle: (key: string) => void;
    onSelect: (node: INodePallete) => void;
    onAdd?: (id: string) => void;
    onDragEnd?: () => void;
}

/**
 * A connector is browsed by APP: one row per collection (Jira, Slack…), which opens in
 * place onto its kind sections (Triggers / Actions / Find & Read / folded Utilities).
 * Multiple connectors may be open at once — that is the whole point of killing the drawer.
 */
const PaletteConnectorBand = ({
    collections,
    isExpanded,
    onToggle,
    onSelect,
    onAdd,
    onDragEnd,
}: IPaletteConnectorBandProps) => {
    const { t } = useLanguage();

    return (
        <>
            {collections.map((collection) => (
                <div key={collection.key}>
                    <PaletteCollectionRow
                        label={collection.label}
                        subtitle={[collection.vendor, t("palette_operations", { count: collection.count })]
                            .filter(Boolean)
                            .join(" · ")}
                        icon={collection.icon}
                        count={collection.count}
                        expanded={isExpanded(collection.key)}
                        onToggle={() => onToggle(collection.key)}
                    />

                    {isExpanded(collection.key) &&
                        collection.sections.map((section) => (
                            <PaletteKindSection
                                key={section.key}
                                section={section}
                                collection={collection.label}
                                onSelect={onSelect}
                                onAdd={onAdd}
                                onDragEnd={onDragEnd}
                            />
                        ))}
                </div>
            ))}
        </>
    );
};

export default PaletteConnectorBand;
