import { X } from "lucide-react";

import { cn } from "@ldc/ui";
import { useDesignerCtx } from "../hooks/use-designer-context";
import { ConfigPanel } from "./config/config-panel";

/**
 * Hosts the ConfigPanel.
 *
 * Desktop (>=860px): an in-flow, fixed 380px right column beside the canvas.
 *
 * Narrow (<860px): a FULL-WIDTH panel that overlays the canvas — an absolute, NON-modal overlay
 * (not a Dialog/Sheet) anchored to the builder's `relative` content row, covering the canvas
 * beneath it. It stays mounted and slides in/out via a `translate-x` transform driven by the
 * selection, so BOTH opening and closing animate smoothly. Dismissed with the ✕ or Escape (the
 * global handler in `form-builder.tsx`).
 */
export function InspectorShell({ isNarrow }: { isNarrow: boolean }) {
    const { selectedNode, selectNode } = useDesignerCtx();

    if (!isNarrow) {
        return (
            <div className="w-[380px] shrink-0 border-l border-border bg-card overflow-hidden">
                <ConfigPanel />
            </div>
        );
    }

    const open = !!selectedNode;
    return (
        <div
            role="region"
            aria-label="Field configuration"
            aria-hidden={!open}
            className={cn(
                "absolute inset-0 z-30 flex flex-col overflow-hidden bg-card shadow-2xl",
                "transition-transform duration-300 ease-in-out",
                open ? "translate-x-0" : "pointer-events-none translate-x-full",
            )}
        >
            <button
                type="button"
                onClick={() => selectNode(null)}
                aria-label="Close field configuration"
                className="absolute top-2 right-2 z-10 rounded-md p-1 text-muted-foreground hover:bg-muted"
            >
                <X className="size-4" />
            </button>
            <ConfigPanel />
        </div>
    );
}
