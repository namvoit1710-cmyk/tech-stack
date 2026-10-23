import { useDroppable } from "@dnd-kit/core";
import { cn } from "@ldc/ui";

// ─── Drop zone ID conventions ──────────────────────────────────────────────────
// All droppable zones use "drop::" prefix so the collision detector can
// distinguish them from sortable field card IDs.

export const DROP_ROOT = "drop::root";
export const dropFlat = (id: string) => `drop::flat::${id}`;
export const dropTable = (id: string) => `drop::table::${id}`;
export const dropArray = (id: string) => `drop::array::${id}`;
export const dropSlot = (id: string, slotId: string) => `drop::slot::${id}::${slotId}`;

export interface ParsedDrop {
    kind: "root" | "flat" | "table" | "array" | "slot";
    containerId?: string;
    slotId?: string;
}

export function parseDrop(id: string): ParsedDrop | null {
    if (id === DROP_ROOT) return { kind: "root" };
    if (!id.startsWith("drop::")) return null;
    const [, kind, cid, slotId] = id.split("::");
    if (kind === "flat") return { kind: "flat", containerId: cid };
    if (kind === "table") return { kind: "table", containerId: cid };
    if (kind === "array") return { kind: "array", containerId: cid };
    if (kind === "slot") return { kind: "slot", containerId: cid, slotId };
    return null;
}

// ─── DroppableZone component ───────────────────────────────────────────────────

interface DroppableZoneProps {
    id: string;
    className?: string;
    children?: React.ReactNode;
}

export function DroppableZone({ id, className, children }: DroppableZoneProps) {
    const { setNodeRef, isOver } = useDroppable({ id });
    return (
        <div
            ref={setNodeRef}
            className={cn(
                "transition-colors duration-150",
                isOver && "ring-2 ring-inset ring-primary/40 bg-primary/5 rounded-lg",
                className
            )}
        >
            {children}
        </div>
    );
}