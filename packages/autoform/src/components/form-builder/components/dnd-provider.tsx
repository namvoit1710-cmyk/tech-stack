import type {
    CollisionDetection,
    DragEndEvent,
    DragOverEvent,
    DragStartEvent
} from "@dnd-kit/core";
import {
    DndContext,
    DragOverlay,
    KeyboardSensor,
    MeasuringStrategy,
    MouseSensor,
    TouchSensor,
    closestCenter,
    pointerWithin,
    rectIntersection,
    useSensor,
    useSensors,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useCallback } from "react";

import { FIELD_DEFS, WRAPPER_DEFS } from "../constants";
import { useDesignerCtx } from "../hooks/use-designer-context";
import type { DesignerNode, PaletteFieldDef, PaletteWrapperDef } from "../types";
import { collectAllKeys, createField } from "../utils/nodes";
import { DROP_ROOT, parseDrop } from "./dnd";

/** Shape stored in dnd-kit's `data.current` for draggables in the designer. */
type ActiveDragData =
    | { type: "palette-field"; def: PaletteFieldDef }
    | { type: "palette-wrapper"; def: PaletteWrapperDef }
    | { type: "node"; nodeId: string; listId?: string };

// ─── Collision: drop:: zones beat sortable cards ──────────────────────────────

const collisionDetection: CollisionDetection = (args) => {
    const activeData = args.active?.data?.current;
    const isFromPalette =
        activeData?.type === "palette-field" || activeData?.type === "palette-wrapper";

    if (isFromPalette) {
        // Palette items: drop:: zones take priority (original behaviour)
        const zones = args.droppableContainers.filter(
            (c) => String(c.id).startsWith("drop::")
        );
        const zoneHits = pointerWithin({ ...args, droppableContainers: zones });
        if (zoneHits.length > 0) return zoneHits;
    }

    // Existing node drag: prefer a card hit (so card-to-card reorder and
    // drop-onto-container-card both work), but if the pointer is over an
    // EMPTY container drop zone (no card to intersect), fall through to zones
    // below so the control still lands inside the container.
    const cards = args.droppableContainers.filter(
        (c) => !String(c.id).startsWith("drop::")
    );
    const cardHits = rectIntersection({ ...args, droppableContainers: cards });
    if (cardHits.length > 0) return cardHits;

    // Then check drop:: zones as last resort
    const zones = args.droppableContainers.filter(
        (c) => String(c.id).startsWith("drop::")
    );
    const zoneHits = pointerWithin({ ...args, droppableContainers: zones });
    if (zoneHits.length > 0) return zoneHits;

    return closestCenter(args);
};

// ─── Helpers to parse listId ───────────────────────────────────────────────────
// listId encodes WHERE a card lives:
//   "drop::root"                  → root level
//   "drop::flat::{containerId}"   → inside DivWrapper / FragmentWrapper .children
//   "drop::slot::{cid}::{slotId}" → inside a tab/step slot .children
//   "drop::table::{containerId}"  → inside TableWrapper .columns

interface ListLocation {
    kind: "root" | "flat" | "slot" | "table" | "array";
    containerId?: string;
    slotId?: string;
}

function parseListId(listId: string): ListLocation {
    const d = parseDrop(listId);
    if (!d) return { kind: "root" };
    return d;
}

function getListItems(nodes: DesignerNode[], loc: ListLocation): DesignerNode[] {
    if (loc.kind === "root") return nodes;
    if (!loc.containerId) return nodes;

    const container = findInTree(nodes, loc.containerId);
    if (container?._kind !== "container") return [];

    if (loc.kind === "flat") return container.children;
    if (loc.kind === "table") return container.columns ?? [];
    if (loc.kind === "array") return container.arrays ?? [];
    if (loc.kind === "slot" && loc.slotId) {
        return container.slots?.find((s) => s._id === loc.slotId)?.children ?? [];
    }
    return [];
}

function findInTree(nodes: DesignerNode[], id: string): DesignerNode | null {
    for (const n of nodes) {
        if (n._id === id) return n;
        if (n._kind === "container") {
            const c = findInTree(n.children, id) ??
                (n.columns ? findInTree(n.columns, id) : null) ??
                (n.arrays ? findInTree(n.arrays, id) : null);
            if (c) return c;
            for (const s of n.slots ?? []) {
                const sc = findInTree(s.children, id);
                if (sc) return sc;
            }
        }
    }
    return null;
}

// ─── DndProvider ─────────────────────────────────────────────────────────────

interface DndProviderProps {
    children: React.ReactNode;
    onActiveId: (id: string | null) => void;
    onOverId: (id: string | null) => void;
}

export function DndProvider({ children, onActiveId, onOverId }: DndProviderProps) {
    const ctx = useDesignerCtx();
    const { nodes } = ctx;

    const sensors = useSensors(
        useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
        useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
    );

    const handleDragStart = useCallback(({ active }: DragStartEvent) => {
        onActiveId(String(active.id));
    }, [onActiveId]);

    const handleDragOver = useCallback(({ over }: DragOverEvent) => {
        onOverId(over ? String(over.id) : null);
    }, [onOverId]);

    const handleDragEnd = useCallback(({ active, over }: DragEndEvent) => {
        onActiveId(null);
        onOverId(null);
        if (!over) return;

        const activeData = active.data.current as ActiveDragData | undefined;
        const targetId = String(over.id);
        const drop = parseDrop(targetId);

        // ────────────────────────────────────────────────────────────────────
        // 1. From palette
        // ────────────────────────────────────────────────────────────────────
        if (activeData?.type === "palette-field") {
            const def: PaletteFieldDef = activeData.def;
            const keys = collectAllKeys(nodes);

            if (drop?.kind === "root") { ctx.addField(def, null, -1); return; }
            if (drop?.kind === "flat" && drop.containerId) { ctx.addField(def, drop.containerId, -1); return; }
            if (drop?.kind === "table" && drop.containerId) { ctx.addField(def, drop.containerId, -1); return; }
            if (drop?.kind === "array" && drop.containerId) { ctx.addField(def, drop.containerId, -1); return; }
            if (drop?.kind === "slot" && drop.containerId && drop.slotId) {
                ctx.updateNode(drop.containerId, (n) => {
                    if (n._kind !== "container" || !n.slots) return n;
                    return {
                        ...n,
                        slots: n.slots.map((s) =>
                            s._id !== drop.slotId
                                ? s
                                : { ...s, children: [...s.children, createField(def, keys)] }
                        ),
                    };
                });
                return;
            }
            // Dropped on a card → insert before it at root
            const idx = nodes.findIndex((n) => n._id === targetId);
            ctx.addField(def, null, idx >= 0 ? idx : -1);
            return;
        }

        if (activeData?.type === "palette-wrapper") {
            const def: PaletteWrapperDef = activeData.def;
            if (drop?.kind === "slot" && drop.containerId && drop.slotId) {
                ctx.addContainerToSlot(def, drop.containerId, drop.slotId, -1);
                return;
            }
            if (!drop || drop.kind === "root") { ctx.addContainer(def, -1); return; }
            const idx = nodes.findIndex((n) => n._id === targetId);
            ctx.addContainer(def, idx >= 0 ? idx : -1);
            return;
        }

        // ────────────────────────────────────────────────────────────────────
        // 2. Reorder / move existing node
        // ────────────────────────────────────────────────────────────────────
        if (activeData?.type === "node") {
            const nodeId: string = activeData.nodeId;
            const fromListId: string = activeData.listId ?? DROP_ROOT;
            if (nodeId === targetId) return;

            const fromLoc = parseListId(fromListId);

            // ── Dropped on a drop:: zone ─────────────────────────────────────
            if (drop) {
                // Same zone → append to end (shouldn't normally happen but safe)
                if (fromListId === targetId) {
                    return;
                }

                // Moving to root
                if (drop.kind === "root") {
                    ctx.moveNode(nodeId, null, -1);
                    return;
                }
                // Moving to a flat container
                if (drop.kind === "flat" && drop.containerId) {
                    ctx.moveNode(nodeId, drop.containerId, -1);
                    return;
                }
                // Moving to table columns
                if (drop.kind === "table" && drop.containerId) {
                    ctx.moveNode(nodeId, drop.containerId, -1);
                    return;
                }
                // Moving to array
                if (drop.kind === "array" && drop.containerId) {
                    ctx.moveNode(nodeId, drop.containerId, -1);
                    return;
                }
                // Moving to a specific slot
                if (drop.kind === "slot" && drop.containerId && drop.slotId) {
                    ctx.moveNodeToSlot(nodeId, drop.containerId, drop.slotId, -1);
                    return;
                }
                return;
            }

            // ── Dropped on another card ──────────────────────────────────────
            // Determine the target card's list from its own sortable data
            const overData = over.data.current as { listId?: string } | undefined;
            const overListId: string = overData?.listId ?? DROP_ROOT;
            const overLoc = parseListId(overListId);

            // Dropped directly onto a CONTAINER card → move inside that container
            // (not reorder beside it). Lets you drag an existing control into a
            // container without removing and re-dragging from the palette.
            const overNode = findInTree(nodes, targetId);
            if (overNode?._kind === "container" && overNode._id !== nodeId) {
                // Guard: don't drop a container into its own subtree
                const movingNode = findInTree(nodes, nodeId);
                const intoSelf =
                    movingNode?._kind === "container" &&
                    !!findInTree([movingNode], overNode._id) &&
                    movingNode._id !== overNode._id;
                if (intoSelf) return;

                if (overNode.slots?.length) {
                    const firstSlot = overNode.slots[0];
                    if (firstSlot) {
                        ctx.moveNodeToSlot(nodeId, overNode._id, firstSlot._id, -1);
                    }
                } else {
                    // flat children / table columns / array items all routed by moveNode
                    ctx.moveNode(nodeId, overNode._id, -1);
                }
                return;
            }

            if (fromListId === overListId) {
                // ── Same list: reorder ──────────────────────────────────────
                const listItems = getListItems(nodes, fromLoc);
                const fromIdx = listItems.findIndex((n) => n._id === nodeId);
                const toIdx = listItems.findIndex((n) => n._id === targetId);

                if (fromIdx < 0 || toIdx < 0) return; // shouldn't happen

                if (fromLoc.kind === "root") {
                    ctx.reorderRootNodes(fromIdx, toIdx);
                } else if (fromLoc.kind === "flat" && fromLoc.containerId) {
                    ctx.reorderInContainer(fromLoc.containerId, fromIdx, toIdx);
                } else if (fromLoc.kind === "slot" && fromLoc.containerId && fromLoc.slotId) {
                    ctx.reorderInSlot(fromLoc.containerId, fromLoc.slotId, fromIdx, toIdx);
                } else if (fromLoc.kind === "table" && fromLoc.containerId) {
                    ctx.reorderInColumns(fromLoc.containerId, fromIdx, toIdx);
                } else if (fromLoc.kind === "array" && fromLoc.containerId) {
                    ctx.reorderInArray(fromLoc.containerId, fromIdx, toIdx);
                }
            } else {
                // ── Cross-list: move to target list before the over card ────
                const overItems = getListItems(nodes, overLoc);
                const overIdx = overItems.findIndex((n) => n._id === targetId);

                if (overLoc.kind === "root") {
                    ctx.moveNode(nodeId, null, overIdx >= 0 ? overIdx : -1);
                } else if (overLoc.kind === "flat" && overLoc.containerId) {
                    ctx.moveNode(nodeId, overLoc.containerId, overIdx >= 0 ? overIdx : -1);
                } else if (overLoc.kind === "table" && overLoc.containerId) {
                    ctx.moveNode(nodeId, overLoc.containerId, overIdx >= 0 ? overIdx : -1);
                } else if (overLoc.kind === "array" && overLoc.containerId) {
                    ctx.moveNode(nodeId, overLoc.containerId, overIdx >= 0 ? overIdx : -1);
                } else if (overLoc.kind === "slot" && overLoc.containerId && overLoc.slotId) {
                    ctx.moveNodeToSlot(nodeId, overLoc.containerId, overLoc.slotId, overIdx >= 0 ? overIdx : -1);
                }
            }
        }
    }, [nodes, ctx]);

    return (
        <DndContext
            sensors={sensors}
            collisionDetection={collisionDetection}
            measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragEnd={handleDragEnd}
        >
            {children}
        </DndContext>
    );
}

// ─── DragOverlay ghost ────────────────────────────────────────────────────────

export function DesignerDragOverlay({ activeId, nodes }: {
    activeId: string | null;
    nodes: DesignerNode[];
}) {
    return (
        <DragOverlay dropAnimation={null}>
            {activeId && <GhostCard id={activeId} nodes={nodes} />}
        </DragOverlay>
    );
}

function GhostCard({ id, nodes }: { id: string; nodes: DesignerNode[] }) {
    const pf = FIELD_DEFS.find((d) => `palette-field::${d.type}` === id);
    if (pf) return (
        <div className="bg-card border-2 border-primary rounded-lg px-3 py-2 shadow-2xl text-xs font-semibold text-foreground w-44 cursor-grabbing select-none">
            {pf.label}
            <span className="ml-2 text-[9px] font-normal text-muted-foreground">{pf.defaultOutputType}</span>
        </div>
    );

    const pw = WRAPPER_DEFS.find((d) => `palette-wrapper::${d.wrapperType}` === id);
    if (pw) return (
        <div className="bg-card border-2 border-primary rounded-lg px-3 py-2 shadow-2xl text-xs font-semibold text-foreground w-44 cursor-grabbing select-none">
            {pw.label}
            <span className="ml-2 text-[9px] font-normal text-muted-foreground">wrapper</span>
        </div>
    );

    const node = findInTree(nodes, id);
    return (
        <div className="bg-card border-2 border-primary rounded-lg px-3 py-2 shadow-2xl text-xs font-semibold text-foreground w-44 cursor-grabbing select-none">
            {node ? (node.fieldConfig.wrapperProps?.label ?? node.key) : id}
        </div>
    );
}