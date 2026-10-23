import { Eye, SquarePen, Trash2 } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@ldc/ui/components/button";
import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";

import { useDesignerCtx } from "../hooks/use-designer-context";
import type { ViewMode } from "../hooks/use-form-builder";

export interface BuilderToolbarProps {
    view: ViewMode;
    setView: (view: ViewMode) => void;
}

export function BuilderToolbar({ view, setView }: BuilderToolbarProps): JSX.Element {
    const { nodes, clearAll } = useDesignerCtx();
    const isBuilder: boolean = view === "builder";

    return (
        <TooltipProvider delayDuration={300}>
            <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-background shrink-0">
                <span className="text-xs text-muted-foreground">
                    {nodes.length} node{nodes.length !== 1 ? "s" : ""}
                </span>

                <div className="flex items-center gap-2">
                    <ToggleGroup
                        type="single"
                        value={view}
                        onValueChange={(v) => v && setView(v as ViewMode)}
                        variant="outline"
                        size="sm"
                    >
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <ToggleGroupItem value="builder" aria-label="Builder" className="size-7 px-0">
                                    <SquarePen className="size-3.5" />
                                </ToggleGroupItem>
                            </TooltipTrigger>
                            <TooltipContent>Builder</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <ToggleGroupItem value="preview" aria-label="Preview" className="size-7 px-0">
                                    <Eye className="size-3.5" />
                                </ToggleGroupItem>
                            </TooltipTrigger>
                            <TooltipContent>Preview</TooltipContent>
                        </Tooltip>
                    </ToggleGroup>

                    {isBuilder && nodes.length > 0 && (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon-sm"
                                    variant="ghost"
                                    onClick={clearAll}
                                    aria-label="Clear all"
                                    className="size-7 text-muted-foreground hover:text-destructive"
                                >
                                    <Trash2 className="size-3.5" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>Clear all</TooltipContent>
                        </Tooltip>
                    )}
                </div>
            </div>
        </TooltipProvider>
    );
}

export default BuilderToolbar;
