import type { INodePallete, IPaletteCollection } from "@/features/workflows/types/node-pallete";
import { useLanguage } from "@/hooks/use-language";
import PaletteCollectionRow from "./palette-collection-row";
import PaletteNodeRow from "./palette-node-row";

interface IPaletteCoreBandProps {
    collections: IPaletteCollection[];
    /** Scoped by the parent — a Core bucket's key shares a namespace with nothing else. */
    isExpanded: (key: string) => boolean;
    onToggle: (key: string) => void;
    onSelect: (node: INodePallete) => void;
    onAdd?: (id: string) => void;
    onDragEnd?: () => void;
}

/**
 * Core is browsed by FUNCTION, so its rows are the buckets the hook folded it into
 * (Triggers, Flow & Logic, …) — not its collection. Each bucket opens in place onto its
 * own nodes; there are no kind sections to draw inside it.
 */
const PaletteCoreBand = ({ collections, isExpanded, onToggle, onSelect, onAdd, onDragEnd }: IPaletteCoreBandProps) => {
    const { t } = useLanguage();

    return (
        <>
            {collections.flatMap((collection) =>
                collection.sections.map((section) => (
                    <div key={section.key}>
                        <PaletteCollectionRow
                            label={section.labelKey ? t(section.labelKey) : section.label}
                            subtitle={section.preview}
                            icon={section.icon}
                            count={section.count}
                            kind={section.kind}
                            expanded={isExpanded(section.key)}
                            onToggle={() => onToggle(section.key)}
                        />

                        {isExpanded(section.key) &&
                            section.items.map((node) => (
                                <PaletteNodeRow
                                    key={node.original?.id ?? node.text}
                                    node={node}
                                    indent
                                    onSelect={onSelect}
                                    onAdd={onAdd}
                                    onDragEnd={onDragEnd}
                                />
                            ))}
                    </div>
                )),
            )}
        </>
    );
};

export default PaletteCoreBand;
