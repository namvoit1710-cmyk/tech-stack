import type { INodePallete, IPaletteSection } from "@/features/workflows/types/node-pallete";
import { tintOf } from "@/features/workflows/utils/palette-taxonomy";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import PaletteNodeRow from "./palette-node-row";

interface IPaletteKindSectionProps {
    section: IPaletteSection;
    collection: string;
    onSelect: (node: INodePallete) => void;
    onAdd?: (id: string) => void;
    onDragEnd?: () => void;
}

const PaletteKindSection = ({ section, collection, onSelect, onAdd, onDragEnd }: IPaletteKindSectionProps) => {
    const { t } = useLanguage();
    const tint = tintOf(section.kind);
    const label = section.labelKey ? t(section.labelKey) : section.label;

    return (
        <div>
            <div className="flex items-center gap-2 px-4 pt-3 pb-1">
                <span className={cn("w-1 h-4 rounded-sm", tint.rule)} />
                <h4 className="text-xs font-semibold uppercase text-muted-foreground">{label}</h4>
                <Badge variant="secondary" className="ml-auto text-[10px]">
                    {section.count}
                </Badge>
            </div>

            {section.items.map((node, i) => (
                <PaletteNodeRow
                    key={`${section.key}-${i}`}
                    node={node}
                    collection={collection}
                    indent
                    onSelect={onSelect}
                    onAdd={onAdd}
                    onDragEnd={onDragEnd}
                />
            ))}
        </div>
    );
};

export default PaletteKindSection;
