import type React from "react";
import { ChevronDown } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible";

export interface CollapsibleSectionProps {
    label: string;
    /** Open on first render. Everything but Live preview / Basics starts closed. */
    defaultOpen?: boolean;
    /** A small count badge next to the label (e.g. hidden-prop count, rule count). */
    count?: number;
    /** `data-testid` on the count badge, for the two call sites a test keys off it by id. */
    countTestId?: string;
    /**
     * Keep the content mounted (CSS-hidden) while closed instead of unmounting it. Only
     * "More settings" needs this — its characterization test asserts the Wrapper/Control
     * Props inputs are in the DOM at initial render even though the section starts closed.
     * Every other section (Advanced (raw props), Validation, Condition) unmounts on close,
     * same as the bespoke `{open && (...)}` toggles this replaces.
     */
    keepMounted?: boolean;
    children: React.ReactNode;
}

/**
 * One titled, collapsible group — uppercase label + chevron + optional count badge, full-width
 * clickable header, a border-top separating it from the section above. Replaces `InspectorSection`
 * (Live preview / Basics) and every bespoke `useState` + button toggle this ticket unifies
 * (More settings, Advanced (raw props) ×3, Validation, Condition) with one @ldc/ui `Collapsible`.
 */
export function CollapsibleSection({
    label,
    defaultOpen = false,
    count,
    countTestId,
    keepMounted = false,
    children,
}: CollapsibleSectionProps) {
    return (
        <Collapsible defaultOpen={defaultOpen} className="border-t border-border first:border-t-0">
            <CollapsibleTrigger className="group flex w-full items-center gap-1.5 px-3 py-2.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 hover:text-foreground">
                {label}
                {typeof count === "number" && (
                    <span
                        data-testid={countTestId}
                        className="min-w-4 px-1 rounded-full bg-muted text-[9px] leading-4 text-muted-foreground tabular-nums normal-case"
                    >
                        {count}
                    </span>
                )}
                <ChevronDown className="size-3.5 shrink-0 ml-auto transition-transform group-data-[state=closed]:-rotate-90" />
            </CollapsibleTrigger>
            {keepMounted ? (
                <CollapsibleContent forceMount className="pb-2 data-[state=closed]:hidden">
                    {children}
                </CollapsibleContent>
            ) : (
                <CollapsibleContent className="pb-2">{children}</CollapsibleContent>
            )}
        </Collapsible>
    );
}

export default CollapsibleSection;
