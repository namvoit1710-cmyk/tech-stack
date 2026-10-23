import { cn } from "@ldc/ui";
import { Star } from "lucide-react";

// SA-2876: GitHub-style run star — [☆ Star | n]. A star is permanent: once
// `starred`, the button is locked (no unstar).
export interface StarButtonProps {
    starred: boolean;
    count: number;
    onStar: () => void;
    disabled?: boolean;
    labels: { star: string; starred: string; hint: string; lockedHint: string };
}

export const StarButton = ({ starred, count, onStar, disabled = false, labels }: StarButtonProps) => (
    <span className="inline-flex overflow-hidden rounded-md border border-border text-xs font-medium">
        <button
            type="button"
            disabled={starred || disabled}
            title={starred ? labels.lockedHint : labels.hint}
            aria-pressed={starred}
            onClick={(e) => {
                e.stopPropagation();
                if (!starred && !disabled) onStar();
            }}
            className={cn(
                "inline-flex items-center gap-1 bg-muted px-2 py-0.5",
                starred ? "cursor-default text-amber-700 dark:text-amber-400" : "cursor-pointer hover:bg-muted/60",
            )}
        >
            <Star className={cn("size-3.5", starred ? "fill-amber-500 text-amber-500" : "text-muted-foreground")} />
            {starred ? labels.starred : labels.star}
        </button>
        <span className="min-w-6 border-l border-border bg-background px-2 py-0.5 text-center tabular-nums">{count}</span>
    </span>
);

export default StarButton;
