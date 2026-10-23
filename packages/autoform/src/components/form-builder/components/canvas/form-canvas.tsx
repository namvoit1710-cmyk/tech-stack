import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { LayoutGrid } from "lucide-react";

import { cn } from "@ldc/ui";
import { useDesignerCtx } from "../../hooks/use-designer-context";
import { DROP_ROOT } from "../dnd";
import { ContainerNode } from "./container-node";
import { FieldCard } from "./field-card";

interface FormCanvasProps {
    activeId: string | null;
    overId: string | null;
}

export function FormCanvas({ activeId, overId }: FormCanvasProps) {
    const { nodes } = useDesignerCtx();
    const rootIds = nodes.map((n) => n._id);
    const isRootOver = overId === DROP_ROOT;

    return (
        <div className="flex flex-col h-full overflow-hidden">
            <div className="flex-1 overflow-y-auto p-3">
                <SortableContext items={rootIds} strategy={verticalListSortingStrategy}>
                    <RootDrop isOver={isRootOver} isEmpty={nodes.length === 0}>
                        {nodes.length === 0
                            ? <EmptyState isOver={isRootOver} />
                            : nodes.map((node) =>
                                node._kind === "container"
                                    ? <ContainerNode key={node._id} container={node} activeId={activeId} overId={overId} />
                                    : <FieldCard key={node._id} field={node} listId={DROP_ROOT} />
                            )
                        }
                    </RootDrop>
                </SortableContext>
            </div>
        </div>
    );
}

function RootDrop({ isOver, isEmpty, children }: {
    isOver: boolean; isEmpty: boolean; children: React.ReactNode;
}) {
    const { setNodeRef } = useDroppable({ id: DROP_ROOT });
    return (
        <div
            ref={setNodeRef}
            className={cn(
                "min-h-full rounded-xl p-2 flex flex-col gap-1.5 transition-colors duration-150",
                isEmpty && "border-2 border-dashed border-muted-foreground/20 items-center justify-center",
                isOver && "!border-2 !border-primary/50 !bg-primary/5"
            )}
        >
            {children}
        </div>
    );
}

function EmptyState({ isOver }: { isOver: boolean }) {
    return (
        <div className={cn("flex flex-col items-center gap-3 text-center transition-colors", isOver && "text-primary")}>
            <div className={cn(
                "w-12 h-12 rounded-xl border-2 border-dashed flex items-center justify-center transition-colors",
                isOver ? "border-primary text-primary" : "border-muted-foreground/20 text-muted-foreground/20"
            )}>
                <LayoutGrid className="w-6 h-6" />
            </div>
            <div>
                <p className={cn("text-sm font-medium", isOver ? "text-primary" : "text-muted-foreground")}>
                    {isOver ? "Release to drop" : "Drag controls here"}
                </p>
                <p className="text-xs text-muted-foreground/60 mt-1">
                    Or double-click items in the palette
                </p>
            </div>
        </div>
    );
}