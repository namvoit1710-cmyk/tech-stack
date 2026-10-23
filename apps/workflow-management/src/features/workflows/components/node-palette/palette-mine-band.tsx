import type { INodePallete, IPaletteCollection } from "@/features/workflows/types/node-pallete";
import { MINE_COLLECTION } from "@/features/workflows/utils/palette-taxonomy";
import { useLanguage } from "@/hooks/use-language";
import PaletteCollectionRow from "./palette-collection-row";
import PaletteKindSection from "./palette-kind-section";
import PaletteNodeRow from "./palette-node-row";

interface IPaletteMineBandProps {
    collections: IPaletteCollection[];
    /** Scoped by the parent: a named collection here can collide with a connector of the
     *  same name — the same reason the connector band's keys are namespaced. */
    isExpanded: (key: string) => boolean;
    onToggle: (key: string) => void;
    onSelect: (node: INodePallete) => void;
    onAdd?: (id: string) => void;
    onDragEnd?: () => void;
}

/**
 * A tenant's own nodes are few, and they are the ones they came for. The DEFAULT collection
 * ("My Nodes") is shown flat, with no row to open first — the band header already says "My
 * Nodes", so a row repeating it would be redundant. But a business author can name their OWN
 * collection ("Finance"): that collection gets a row exactly like a connector's, expanding
 * in place onto its kind sections. The CUSTOM badge itself is no longer this band's concern —
 * `PaletteNodeRow` computes it from `provider` — so it renders correctly in either shape.
 */
const PaletteMineBand = ({ collections, isExpanded, onToggle, onSelect, onAdd, onDragEnd }: IPaletteMineBandProps) => {
    const { t } = useLanguage();

    return (
        <>
            {collections.flatMap((collection) => {
                if (collection.key === MINE_COLLECTION) {
                    return collection.sections.flatMap((section) =>
                        section.items.map((node) => (
                            <PaletteNodeRow
                                key={node.original?.id ?? node.text}
                                node={node}
                                subtitle={section.labelKey ? t(section.labelKey) : section.label}
                                onSelect={onSelect}
                                onAdd={onAdd}
                                onDragEnd={onDragEnd}
                            />
                        )),
                    );
                }

                return [
                    <div key={collection.key}>
                        <PaletteCollectionRow
                            label={collection.label}
                            subtitle={t("palette_operations", { count: collection.count })}
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
                    </div>,
                ];
            })}
        </>
    );
};

export default PaletteMineBand;
