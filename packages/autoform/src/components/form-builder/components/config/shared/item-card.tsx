import type React from "react";
import { X } from "lucide-react";

/**
 * One shared compact-control height for every Input/Textarea in the config folder (no `size`
 * prop of their own, unlike Select/Switch). `Input` is fixed `h-9`; `twMerge` (in `@ldc/ui`'s
 * `cn`) resolves the conflict in favour of whichever height comes later in the class string, so
 * a plain `h-8` here is enough — no `h-7`, no `!important` hacks.
 */
export const COMPACT_FIELD_CLASS = "h-8 text-xs";

/**
 * Horizontal gutter for a section's rows when they sit directly inside a `CollapsibleSection`
 * (whose content has no side padding of its own, only `Field`/explicit wrappers do). Condition,
 * Advanced (raw props) and Validation apply this to their `ItemCard`/`ItemList` so the rows
 * aren't flush against the panel edge, matching Basics/Live preview.
 */
export const SECTION_ROWS_PADDING_CLASS = "px-3";

export interface ItemCardProps {
    /** Short fields (Key/Type/Method/Watch field), rendered inline in the card's header row. */
    header?: React.ReactNode;
    /** Wide fields (Value/Expression/Message), stacked full-width below the header. */
    children?: React.ReactNode;
    onRemove?: () => void;
    removeLabel?: string;
}

/**
 * One bordered card — the single layout every list editor (Wrapper/Control Props, Validation
 * rules) and the Condition rule share. `header` holds the short, inline fields plus the remove
 * ✕; anything wide goes in `children`, stacked full-width beneath it.
 */
export function ItemCard({ header, children, onRemove, removeLabel = "Remove" }: ItemCardProps) {
    const hasHeaderRow = Boolean(header) || Boolean(onRemove);
    return (
        <div className="border border-border rounded-md p-2 space-y-1.5">
            {hasHeaderRow && (
                <div className="flex items-center gap-1">
                    {header && <div className="flex flex-1 items-center gap-1 min-w-0">{header}</div>}
                    {onRemove && (
                        <button
                            type="button"
                            aria-label={removeLabel}
                            onClick={onRemove}
                            className="p-1 text-muted-foreground hover:text-destructive shrink-0 ml-auto"
                        >
                            <X className="w-3 h-3" />
                        </button>
                    )}
                </div>
            )}
            {children && <div className="flex flex-col gap-1.5">{children}</div>}
        </div>
    );
}

export interface ItemListProps {
    isEmpty: boolean;
    emptyLabel?: string;
    addLabel: string;
    onAdd: () => void;
    children?: React.ReactNode;
}

/** The empty-state + full-width dashed "Add" button every `ItemCard` list shares. */
export function ItemList({ isEmpty, emptyLabel = "Nothing here yet", addLabel, onAdd, children }: ItemListProps) {
    return (
        <div className="space-y-1.5">
            {isEmpty ? (
                <div className="border border-dashed border-border rounded-md px-3 py-2 text-[11px] text-muted-foreground text-center">
                    {emptyLabel}
                </div>
            ) : (
                children
            )}
            <button
                type="button"
                onClick={onAdd}
                className="w-full border border-dashed border-border rounded-md py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:border-foreground/50"
            >
                {addLabel}
            </button>
        </div>
    );
}

export default ItemCard;
