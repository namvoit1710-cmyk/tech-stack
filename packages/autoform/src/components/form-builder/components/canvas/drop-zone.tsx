import { useDroppable } from "@dnd-kit/core";
import { cn } from "@ldc/ui";

interface DropZoneProps {
    id: string;          // unique droppable id
    children?: React.ReactNode;
    empty?: boolean;     // show hint when no children
    horizontal?: boolean;
    className?: string;
}

export function DropZone({ id, children, empty, horizontal, className }: DropZoneProps) {
    const { setNodeRef, isOver } = useDroppable({ id });

    return (
        <div
            ref={setNodeRef}
            className={cn(
                "transition-colors duration-150 rounded-lg",
                horizontal ? "flex flex-row flex-wrap gap-2" : "flex flex-col gap-1.5",
                isOver
                    ? "bg-primary/5 ring-2 ring-primary/40 ring-inset"
                    : empty
                        ? "border-2 border-dashed border-muted-foreground/20 hover:border-muted-foreground/40"
                        : "",
                className
            )}
        >
            {empty && !children ? (
                <div className={cn(
                    "flex items-center justify-center text-xs text-muted-foreground/50 select-none",
                    isOver ? "text-primary/60" : ""
                )}
                    style={{ minHeight: 40 }}
                >
                    {isOver ? "Release to drop" : "Drop here"}
                </div>
            ) : children}
        </div>
    );
}