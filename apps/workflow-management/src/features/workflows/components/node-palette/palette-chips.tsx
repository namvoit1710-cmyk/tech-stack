import type { IMenuItem } from "@/features/workflows/types/node-pallete";
import { Button } from "@ldc/ui/components/button";

interface IPaletteChipsProps {
    items: IMenuItem[];
    onPick: (item: IMenuItem) => void;
}

const PaletteChips = ({ items, onPick }: IPaletteChipsProps) => {
    if (items.length === 0) return null;

    return (
        <div className="flex flex-wrap gap-2 px-4">
            {items.map((item, i) => (
                <Button
                    key={`${item.original?.id ?? item.text}-${i}`}
                    size="sm"
                    variant="outline"
                    className="h-7 rounded-full text-xs"
                    onClick={() => onPick(item)}
                >
                    {/* A chip IS a node, so it wears the node's own colour — same rule as a leaf row. */}
                    <span
                        className="size-1.5 rounded-full mr-1.5"
                        style={{ backgroundColor: item.original?.color || "currentColor" }}
                    />
                    {item.text}
                </Button>
            ))}
        </div>
    );
};

export default PaletteChips;
