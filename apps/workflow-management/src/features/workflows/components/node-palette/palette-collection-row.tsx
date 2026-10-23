import { tintOf } from "@/features/workflows/utils/palette-taxonomy";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";

interface IPaletteCollectionRowProps {
    label: string;
    subtitle?: string;
    icon?: string;
    count: number;
    /** Tints the icon tile by kind (Core buckets — a bucket is a GROUP, it has no colour of its own).
     *  A connector row passes nothing and stays neutral. */
    kind?: string;
    expanded: boolean;
    onToggle: () => void;
}

const PaletteCollectionRow = ({ label, subtitle, icon, count, kind, expanded, onToggle }: IPaletteCollectionRowProps) => {
    const tint = kind ? tintOf(kind) : undefined;

    return (
        <div
            role="button"
            aria-expanded={expanded}
            // Announcing `role="button"` without making it reachable is worse than not
            // announcing it: a screen-reader user is told to press it and then cannot.
            tabIndex={0}
            className="flex items-center justify-between hover:bg-muted cursor-pointer px-4 py-2"
            onClick={onToggle}
            onKeyDown={(e) => {
                if (e.key !== "Enter" && e.key !== " ") return;
                e.preventDefault(); // Space would otherwise scroll the palette
                onToggle();
            }}
        >
            <div className="flex items-center gap-3">
                <div className={cn("p-1 rounded-md", tint ? [tint.bg, tint.text] : "bg-muted text-muted-foreground")}>
                    <DynamicNodeIcon className="size-5" name={icon ?? "group"} fallbackIconName="group" strokeWidth={1} />
                </div>
                <div className="flex flex-col">
                    <h3 className="text-sm">{label}</h3>
                    {subtitle && <span className="text-xs text-muted-foreground line-clamp-1">{subtitle}</span>}
                </div>
            </div>

            <div className="flex items-center gap-2">
                <Badge variant="secondary" className="text-[10px]">
                    {count}
                </Badge>
                {expanded ? (
                    <ChevronDownIcon className="size-4" strokeWidth={1.5} />
                ) : (
                    <ChevronRightIcon className="size-4" strokeWidth={1.5} />
                )}
            </div>
        </div>
    );
};

export default PaletteCollectionRow;
