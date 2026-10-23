import type { IMenuItem, INodePallete } from "@/features/workflows/types/node-pallete";
import { nodeTileStyle, stripCollectionPrefix } from "@/features/workflows/utils/palette-taxonomy";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { mapNodeToEditorNode } from "../../utils/node-mapper-utils";

interface IPaletteNodeRowProps {
    node: IMenuItem;
    /** When the row sits inside its own collection's group, drop the redundant prefix. */
    collection?: string;
    subtitle?: string;
    indent?: boolean;
    onSelect: (node: INodePallete) => void;
    /** Record a recent. Fired on click AND on drag-start (a cancelled drag still counts). */
    onAdd?: (id: string) => void;
    onDragEnd?: () => void;
}

const PaletteNodeRow = ({ node, collection, subtitle, indent, onSelect, onAdd, onDragEnd }: IPaletteNodeRowProps) => {
    const { t } = useLanguage();
    const original = node.original;
    // SA-1930: the CUSTOM badge follows the NODE, not the band it happens to render in. A
    // business author's node can land in the connector band (their collection also ships a
    // worker node — see bandOfCollection's worker-wins priority) and must still be badged.
    const custom = original?.provider === "business";
    // The node's OWN colour, from the BE. `kind` is expressed by the section it sits
    // in — never by repainting the node (that would erase the author's ColorPicker
    // choice from SA-1736, and the per-node colours the Core specs carry).
    const label = collection ? stripCollectionPrefix(node.text, collection) : node.text;
    const record = () => onAdd?.(original?.id ?? "");

    return (
        <div
            className={cn("flex items-center justify-between hover:bg-muted cursor-pointer px-4", indent && "pl-8")}
            onClick={() => {
                record();
                onSelect(original!);
            }}
            draggable
            onDragStart={(e) => {
                record();
                e.dataTransfer.setData("nodeType", JSON.stringify(mapNodeToEditorNode(original!)));
            }}
            onDragEnd={() => onDragEnd?.()}
        >
            <div className="flex items-center gap-3 py-2">
                <div className="p-1 rounded-md" style={nodeTileStyle(original)}>
                    <DynamicNodeIcon className="size-5" name={node.icon ?? "group"} strokeWidth={1} />
                </div>
                <div className="flex flex-col">
                    <h3 className="whitespace-nowrap line-clamp-1 text-sm">{label}</h3>
                    {subtitle && <span className="text-xs text-muted-foreground">{subtitle}</span>}
                </div>
            </div>
            {custom && (
                <Badge variant="outline" className="text-[10px] tracking-wide">
                    {t("palette_custom_badge")}
                </Badge>
            )}
        </div>
    );
};

export default PaletteNodeRow;
