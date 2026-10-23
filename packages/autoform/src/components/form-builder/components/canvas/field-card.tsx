import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Copy, GripVertical, Trash2 } from "lucide-react";

import { cn } from "@ldc/ui";
import { OUTPUT_TYPE_COLORS } from "../../constants";
import { useDesignerCtx } from "../../hooks/use-designer-context";
import type { DesignerField } from "../../types";

interface FieldCardProps {
    field: DesignerField;
    /**
     * The droppable zone id this card currently lives in.
     * Stored in useSortable data so dnd-provider knows which list
     * the card belongs to when computing reorder / cross-list moves.
     *
     * Format mirrors dnd-utils drop zone ids:
     *   "drop::root"
     *   "drop::flat::{containerId}"
     *   "drop::slot::{containerId}::{slotId}"
     *   "drop::table::{containerId}"
     */
    listId: string;
}

export function FieldCard({ field, listId }: FieldCardProps) {
    const { selectedId, selectNode, deleteNode, duplicateNode } = useDesignerCtx();
    const isSelected = selectedId === field._id;
    const isRequired = field.fieldConfig.rules?.some((r) => r.method === "required");
    const hasCondition = !!field.fieldConfig.condition?.fieldKey;

    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging,
    } = useSortable({
        id: field._id,
        data: {
            type: "node",
            nodeId: field._id,
            /** ← key piece: tells dnd-provider which list this card lives in */
            listId,
        },
    });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.3 : 1,
        zIndex: isDragging ? 999 : undefined,
    };

    const typeCls = OUTPUT_TYPE_COLORS[field.outputType] ?? "bg-gray-100 text-gray-600";

    return (
        <div
            ref={setNodeRef}
            style={style}
            onClick={() => selectNode(field._id)}
            className={cn(
                "group flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer select-none",
                "bg-card transition-colors duration-100",
                isSelected
                    ? "border-primary ring-1 ring-primary/30"
                    : "border-border hover:border-muted-foreground/40",
                isDragging && "shadow-xl"
            )}
        >
            {/* Drag handle — only handle starts the drag */}
            <button
                {...attributes}
                {...listeners}
                className="cursor-grab active:cursor-grabbing text-muted-foreground/30 hover:text-muted-foreground flex-shrink-0 touch-none"
                onClick={(e) => e.stopPropagation()}
                tabIndex={-1}
            >
                <GripVertical className="w-3.5 h-3.5" />
            </button>

            {/* Output type badge */}
            <span className={cn("text-[9px] font-semibold px-1.5 py-0.5 rounded flex-shrink-0", typeCls)}>
                {field.outputType}
            </span>

            {/* Info */}
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                    <span className="text-xs font-medium text-foreground truncate">
                        {field.fieldConfig.wrapperProps?.label ?? field.key}
                    </span>
                    {isRequired && <span className="text-destructive text-xs leading-none flex-shrink-0">*</span>}
                    {hasCondition && <span className="text-[9px] bg-amber-100 text-amber-700 px-1 rounded flex-shrink-0">cond</span>}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5 truncate">
                    <code className="text-[9px]">{field.key}</code>
                    <span className="mx-1 opacity-40">·</span>
                    <span>{field.fieldConfig.fieldControl}</span>
                    {listId !== "drop::root" && (
                        <span className="ml-1 opacity-30">· nested</span>
                    )}
                </div>
            </div>

            {/* Actions */}
            <div
                className={cn(
                    "flex items-center gap-0.5 flex-shrink-0 transition-opacity",
                    isSelected ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                )}
                onClick={(e) => e.stopPropagation()}
            >
                <button
                    onClick={() => duplicateNode(field._id)}
                    className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                    title="Duplicate"
                >
                    <Copy className="w-3 h-3" />
                </button>
                <button
                    onClick={() => deleteNode(field._id)}
                    className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                    title="Delete"
                >
                    <Trash2 className="w-3 h-3" />
                </button>
            </div>
        </div>
    );
}