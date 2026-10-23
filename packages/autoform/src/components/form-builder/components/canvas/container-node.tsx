import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Copy, GripVertical, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";

import { cn } from "@ldc/ui";
import { useDesignerCtx } from "../../hooks/use-designer-context";
import { useOpenPalette } from "../../hooks/use-palette-trigger";
import type { DesignerContainer, DesignerSlot } from "../../types";
import { ODATA_SLOT_NAMES } from "../../constants";
import { DROP_ROOT, DroppableZone, dropArray, dropFlat, dropSlot, dropTable } from "../dnd";
import { FieldCard } from "./field-card";

const ICONS: Record<string, string> = {
    FormItemWrapper: "▤",
    TableWrapper: "▦",
    OdataWrapper: "▦",
    TabWrapper: "⊞",
    SequenceWrapper: "▷",
    DivWrapper: "□",
    FragmentWrapper: "◇",
};

interface ContainerNodeProps {
    container: DesignerContainer;
    activeId: string | null;
    overId: string | null;
}

export function ContainerNode({ container, activeId, overId }: ContainerNodeProps) {
    const { selectedId, selectNode, deleteNode, duplicateNode } = useDesignerCtx();
    const openPalette = useOpenPalette();
    const isSelected = selectedId === container._id;
    const wt = container.wrapperType;
    const hasSlots = wt === "TabWrapper" || wt === "SequenceWrapper";
    const [activeSlotIdx, setActiveSlotIdx] = useState(0);

    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
        id: container._id,
        data: { type: "node", nodeId: container._id, listId: DROP_ROOT },
    });

    return (
        <div
            ref={setNodeRef}
            data-container-id={container._id}
            style={{
                transform: CSS.Transform.toString(transform),
                transition,
                opacity: isDragging ? 0.35 : 1,
            }}
            className={cn(
                "rounded-xl border transition-colors",
                isSelected ? "border-primary ring-1 ring-primary/30" : "border-border",
                isDragging && "shadow-xl z-50"
            )}
        >
            {/* ── Header ── */}
            <div
                className="flex items-center gap-2 px-3 py-2 rounded-t-xl bg-muted/40 border-b border-border cursor-pointer select-none"
                onClick={() => selectNode(container._id)}
            >
                <button
                    {...attributes}
                    {...listeners}
                    className="cursor-grab active:cursor-grabbing text-muted-foreground/40 hover:text-muted-foreground touch-none flex-shrink-0"
                    onClick={(e) => e.stopPropagation()}
                >
                    <GripVertical className="w-3.5 h-3.5" />
                </button>

                <span className="text-sm shrink-0">{ICONS[wt] ?? "□"}</span>

                <span className="text-xs font-semibold text-foreground flex-1 truncate">
                    {container.fieldConfig.wrapperProps?.label ?? container.key}
                </span>

                <div
                    className="flex items-center gap-0.5 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                >
                    <button
                        onClick={(e) => {
                            const r = e.currentTarget.getBoundingClientRect();
                            const activeSlotId = hasSlots
                                ? (container.slots ?? [])[activeSlotIdx]?._id
                                : undefined;
                            openPalette(container._id, { x: r.left, y: r.bottom + 4 }, undefined, activeSlotId);
                        }}
                        className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                        title="Add control inside"
                    >
                        <Plus className="w-3 h-3" />
                    </button>
                    <button
                        onClick={() => duplicateNode(container._id)}
                        className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                        title="Duplicate"
                    >
                        <Copy className="w-3 h-3" />
                    </button>
                    <button
                        onClick={() => deleteNode(container._id)}
                        className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                        title="Delete"
                    >
                        <Trash2 className="w-3 h-3" />
                    </button>
                </div>
            </div>

            {/* ── Body ── */}
            <div className="p-2.5 bg-background rounded-b-xl">
                {wt === "TableWrapper" && (
                    <TableBody container={container} overId={overId} />
                )}
                {wt === "OdataWrapper" && (
                    <OdataBody container={container} overId={overId} />
                )}
                {hasSlots && (
                    <SlotsBody container={container} activeId={activeId} overId={overId} activeSlotIdx={activeSlotIdx} setActiveSlotIdx={setActiveSlotIdx} />
                )}
                {wt === "FragmentWrapper" && container.outputType === "array" && (
                    <ArrayBody container={container} overId={overId} />
                )}
                {wt === "FragmentWrapper" && container.outputType === "object" && (
                    <FlatBody container={container} overId={overId} />
                )}
                {!["TableWrapper", "OdataWrapper", "TabWrapper", "SequenceWrapper", "FragmentWrapper"].includes(wt) && (
                    <FlatBody container={container} overId={overId} />
                )}
            </div>
        </div>
    );
}

// ─── Reusable empty hint ──────────────────────────────────────────────────────

function DropHint({ isOver }: { isOver: boolean }) {
    return (
        <p className={cn(
            "text-xs text-center py-3 select-none",
            isOver ? "text-primary" : "text-muted-foreground/40"
        )}>
            {isOver ? "Release to drop" : "Drop fields here"}
        </p>
    );
}

// ─── FlatBody — DivWrapper / FragmentWrapper / FormItemWrapper ────────────────

function FlatBody({ container, overId }: { container: DesignerContainer; overId: string | null }) {
    const dzId = dropFlat(container._id);
    const isOver = overId === dzId;
    const ids = container.children.map((c) => c._id);

    return (
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <DroppableZone
                id={dzId}
                className={cn(
                    "min-h-[52px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed transition-all duration-150",
                    isOver
                        ? "border-primary bg-primary/5"
                        : "border-muted-foreground/20 hover:border-muted-foreground/30"
                )}
            >
                {container.children.length === 0
                    ? <DropHint isOver={isOver} />
                    : container.children.map((field) => (
                        <FieldCard key={field._id} field={field} listId={dzId} />
                    ))
                }
            </DroppableZone>
        </SortableContext>
    );
}

function ArrayBody({ container, overId }: { container: DesignerContainer; overId: string | null }) {
    const dzId = dropArray(container._id);
    const isOver = overId === dzId;
    const cols = container.arrays ?? [];
    const ids = cols.map((c) => c._id);

    return (
        <div className="flex flex-col gap-2">
            <div className="flex flex-col gap-0.5">
                <p className="text-[10px] font-medium text-muted-foreground/60 px-0.5 mb-0.5">
                    Arrays
                </p>
                <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                    <DroppableZone
                        id={dzId}
                        className={cn(
                            "min-h-[48px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed transition-all duration-150",
                            isOver
                                ? "border-primary bg-primary/5"
                                : "border-muted-foreground/20 hover:border-muted-foreground/30"
                        )}
                    >
                        {cols.length === 0 ? (
                            <p className={cn(
                                "text-xs text-center py-2 select-none",
                                isOver ? "text-primary" : "text-muted-foreground/40"
                            )}>
                                {isOver ? "Release to add column" : "Drop fields here to add columns"}
                            </p>
                        ) : (
                            cols.map((col) => (
                                <FieldCard key={col._id} field={col} listId={dzId} />
                            ))
                        )}
                    </DroppableZone>
                </SortableContext>
            </div>
        </div>
    );
}

// ─── TableBody ────────────────────────────────────────────────────────────────
// Columns are shown as full FieldCards (drag + click-to-edit) in a list,
// plus a drop zone to add more. A read-only table preview sits below.

function TableBody({ container, overId }: { container: DesignerContainer; overId: string | null }) {
    const dzId = dropTable(container._id);
    const isOver = overId === dzId;
    const cols = container.columns ?? [];
    const ids = cols.map((c) => c._id);

    return (
        <div className="flex flex-col gap-2">
            {/* Columns list — full FieldCards, sortable */}
            <div className="flex flex-col gap-0.5">
                <p className="text-[10px] font-medium text-muted-foreground/60 px-0.5 mb-0.5">
                    Columns
                </p>
                <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                    <DroppableZone
                        id={dzId}
                        className={cn(
                            "min-h-[48px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed transition-all duration-150",
                            isOver
                                ? "border-primary bg-primary/5"
                                : "border-muted-foreground/20 hover:border-muted-foreground/30"
                        )}
                    >
                        {cols.length === 0 ? (
                            <p className={cn(
                                "text-xs text-center py-2 select-none",
                                isOver ? "text-primary" : "text-muted-foreground/40"
                            )}>
                                {isOver ? "Release to add column" : "Drop fields here to add columns"}
                            </p>
                        ) : (
                            cols.map((col) => (
                                <FieldCard key={col._id} field={col} listId={dzId} />
                            ))
                        )}
                    </DroppableZone>
                </SortableContext>
            </div>

            {/* Read-only table preview */}
            {cols.length > 0 && (
                <div className="overflow-x-auto rounded-md border border-border mt-1">
                    <table className="w-full text-xs table-fixed">
                        <thead className="bg-muted/60">
                            <tr>
                                {cols.map((col) => (
                                    <th
                                        key={col._id}
                                        className="px-2 py-1.5 text-left text-muted-foreground font-medium border-r border-border last:border-r-0 truncate"
                                    >
                                        {col.fieldConfig.wrapperProps?.label ?? col.key}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {[0, 1].map((ri) => (
                                <tr key={ri} className="border-t border-border">
                                    {cols.map((col) => (
                                        <td
                                            key={col._id}
                                            className="px-2 py-1.5 border-r border-border last:border-r-0 text-muted-foreground/30 italic text-[10px]"
                                        >
                                            row {ri + 1}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

// ─── OdataBody ────────────────────────────────────────────────────────────
// Columns are shown as full FieldCards (drag + click-to-edit) in a list,
// plus a drop zone to add more. Named slot zones for filter, more-action, row-more-action.

function OdataBody({ container, overId }: { container: DesignerContainer; overId: string | null }) {
    const openPalette = useOpenPalette();
    const dzId = dropTable(container._id);
    const isOver = overId === dzId;
    const cols = container.columns ?? [];
    const ids = cols.map((c) => c._id);

    const handleSlotRightClick = (e: React.MouseEvent, slotName: string) => {
        e.preventDefault();
        e.stopPropagation();
        openPalette(container._id, { x: e.clientX, y: e.clientY }, slotName);
    };

    return (
        <div className="flex flex-col gap-2">
            {/* Columns — identical to TableBody columns section */}
            <div className="flex flex-col gap-0.5">
                <p className="text-[10px] font-medium text-muted-foreground/60 px-0.5 mb-0.5">
                    Columns
                </p>
                <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                    <DroppableZone
                        id={dzId}
                        className={cn(
                            "min-h-[48px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed transition-all duration-150",
                            isOver
                                ? "border-primary bg-primary/5"
                                : "border-muted-foreground/20 hover:border-muted-foreground/30"
                        )}
                    >
                        {cols.length === 0 ? (
                            <p className={cn(
                                "text-xs text-center py-2 select-none",
                                isOver ? "text-primary" : "text-muted-foreground/40"
                            )}>
                                {isOver ? "Release to add column" : "Drop fields here to add columns"}
                            </p>
                        ) : (
                            cols.map((col) => (
                                <FieldCard key={col._id} field={col} listId={dzId} />
                            ))
                        )}
                    </DroppableZone>
                </SortableContext>
            </div>

            {/* Named slot zones */}
            {ODATA_SLOT_NAMES.map((slotName) => {
                const fields = container.children.filter(
                    (f) => f.fieldConfig.slot === slotName
                );
                return (
                    <div key={slotName} className="flex flex-col gap-0.5">
                        <p
                            className="text-[10px] font-medium text-muted-foreground/60 px-0.5 mb-0.5 cursor-context-menu select-none"
                            title="Right-click to add field"
                            onContextMenu={(e) => handleSlotRightClick(e, slotName)}
                        >
                            {slotName}
                        </p>
                        <div
                            className="min-h-[36px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed border-muted-foreground/20 hover:border-muted-foreground/30 transition-all duration-150"
                            onContextMenu={(e) => handleSlotRightClick(e, slotName)}
                        >
                            {fields.length === 0 ? (
                                <p className="text-xs text-center py-1 text-muted-foreground/40 select-none">
                                    Right-click to add field
                                </p>
                            ) : (
                                fields.map((f) => (
                                    <FieldCard key={f._id} field={f} listId={`odata-slot-${slotName}`} />
                                ))
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

// ─── SlotsBody — TabWrapper / SequenceWrapper ─────────────────────────────────

function SlotsBody({ container, activeId, overId, activeSlotIdx, setActiveSlotIdx }: { container: DesignerContainer; activeId: string | null; overId: string | null; activeSlotIdx: number; setActiveSlotIdx: (i: number) => void }) {
    const { addSlot, removeSlot, updateSlotLabel } = useDesignerCtx();
    const slots = container.slots ?? [];
    const isTab = container.wrapperType === "TabWrapper";

    return (
        <div className="flex flex-col gap-2">
            {/* Tab / Step bar */}
            <div className="flex items-center gap-1 flex-wrap">
                {slots.map((slot, idx) => (
                    <SlotTab
                        key={slot._id}
                        slot={slot}
                        index={idx}
                        active={idx === activeSlotIdx}
                        isTab={isTab}
                        onClick={() => setActiveSlotIdx(idx)}
                        onLabelChange={(l) => updateSlotLabel(container._id, slot._id, l)}
                        onRemove={
                            slots.length > 1
                                ? () => {
                                    removeSlot(container._id, slot._id);
                                    setActiveSlotIdx(Math.max(0, idx - 1));
                                }
                                : undefined
                        }
                    />
                ))}
                <button
                    onClick={() => {
                        addSlot(container._id);
                        setActiveSlotIdx(slots.length);
                    }}
                    className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                    title={`Add ${isTab ? "tab" : "step"}`}
                >
                    <Plus className="w-3.5 h-3.5" />
                </button>
            </div>

            {/* Active slot drop zone with FieldCards */}
            {slots[activeSlotIdx] && (
                <div data-slot-id={slots[activeSlotIdx]._id}>
                    <SlotDropZone
                        slot={slots[activeSlotIdx]}
                        containerId={container._id}
                        activeId={activeId}
                        overId={overId}
                    />
                </div>
            )}
        </div>
    );
}

function SlotTab({ slot, index, active, isTab, onClick, onLabelChange, onRemove }: {
    slot: DesignerSlot;
    index: number;
    active: boolean;
    isTab: boolean;
    onClick: () => void;
    onLabelChange: (l: string) => void;
    onRemove?: () => void;
}) {
    const [editing, setEditing] = useState(false);

    return (
        <div
            onClick={onClick}
            className={cn(
                "flex items-center gap-1 px-2.5 py-1 rounded-md text-xs cursor-pointer transition-colors select-none",
                active
                    ? "bg-primary/10 text-primary font-medium"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
            )}
        >
            {!isTab && (
                <span className="text-[9px] text-muted-foreground mr-0.5">{index + 1}.</span>
            )}
            {editing ? (
                <input
                    autoFocus
                    className="w-16 bg-transparent outline-none text-xs"
                    value={slot.label}
                    onChange={(e) => onLabelChange(e.target.value)}
                    onBlur={() => setEditing(false)}
                    onKeyDown={(e) => e.key === "Enter" && setEditing(false)}
                    onClick={(e) => e.stopPropagation()}
                />
            ) : (
                <span onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); }}>
                    {slot.label}
                </span>
            )}
            {onRemove && (
                <button
                    onClick={(e) => { e.stopPropagation(); onRemove(); }}
                    className="text-muted-foreground/50 hover:text-destructive ml-0.5"
                >
                    <X className="w-2.5 h-2.5" />
                </button>
            )}
        </div>
    );
}

function SlotDropZone({ slot, containerId, activeId, overId }: {
    slot: DesignerSlot;
    containerId: string;
    activeId: string | null;
    overId: string | null;
}) {
    const dzId = dropSlot(containerId, slot._id);
    const isOver = overId === dzId;
    const ids = slot.children.map((c) => c._id);

    return (
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <DroppableZone
                id={dzId}
                className={cn(
                    "min-h-[64px] rounded-lg p-1.5 flex flex-col gap-1.5 border border-dashed transition-all duration-150",
                    isOver
                        ? "border-primary bg-primary/5"
                        : "border-muted-foreground/20 hover:border-muted-foreground/30"
                )}
            >
                {slot.children.length === 0
                    ? <DropHint isOver={isOver} />
                    : slot.children.map((child) =>
                        child._kind === "container" ? (
                            <ContainerNode key={child._id} container={child} activeId={activeId} overId={overId} />
                        ) : (
                            <FieldCard key={child._id} field={child} listId={dzId} />
                        )
                    )
                }
            </DroppableZone>
        </SortableContext>
    );
}
