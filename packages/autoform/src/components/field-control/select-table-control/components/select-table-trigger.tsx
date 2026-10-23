import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { LayoutGrid, X } from "lucide-react";
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ISelectTableTriggerProps, OptionRecord } from "../types/types";

interface ISingleTriggerProps {
    label: string;
    placeholder?: string;
    disabled?: boolean;
    hasValue: boolean;
    onOpen: () => void;
    onClear: () => void;
}

function SingleTrigger({ label, placeholder, disabled, hasValue, onOpen, onClear }: ISingleTriggerProps) {
    return (
        <div className="relative flex items-center w-full">
            <Input
                readOnly
                value={label}
                placeholder={placeholder}
                disabled={disabled}
                className="pr-16 cursor-pointer"
                onClick={onOpen}
            />
            <div className="absolute right-1 flex items-center gap-0.5">
                {hasValue && (
                    <button
                        type="button"
                        aria-label="Clear selection"
                        disabled={disabled}
                        onClick={(e) => { e.stopPropagation(); onClear(); }}
                        className="p-1 rounded text-muted-foreground hover:text-foreground disabled:opacity-50"
                    >
                        <X className="size-3.5" />
                    </button>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Open table"
                    disabled={disabled}
                    onClick={onOpen}
                    className="size-7"
                >
                    <LayoutGrid className="size-3.5" />
                </Button>
            </div>
        </div>
    );
}

interface IMultiTriggerProps {
    items: OptionRecord[];
    labelKey: string;
    placeholder?: string;
    disabled?: boolean;
    onOpen: () => void;
    onClear: () => void;
}

function MultiTrigger({ items, labelKey, placeholder, disabled, onOpen, onClear }: IMultiTriggerProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chipRefs = useRef<(HTMLSpanElement | null)[]>([]);
    const [visibleCount, setVisibleCount] = useState(items.length);

    const recalculate = () => {
        const container = containerRef.current;
        if (!container || items.length === 0) {
            setVisibleCount(items.length);
            return;
        }
        // Reserve space for badge (~40px) + open button (~32px) + gap
        const RESERVED = 80;
        const maxWidth = container.offsetWidth - RESERVED;
        let used = 0;
        let count = 0;
        for (const chip of chipRefs.current) {
            if (!chip) break;
            used += chip.offsetWidth + 4;
            if (used > maxWidth) break;
            count++;
        }
        setVisibleCount(count);
    };

    useLayoutEffect(() => {
        recalculate();
    }, [items]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        const ro = new ResizeObserver(() => recalculate());
        ro.observe(container);
        return () => ro.disconnect();
    }, [items]);

    const overflowCount = items.length - visibleCount;

    return (
        <div
            ref={containerRef}
            className={cn(
                "flex items-center w-full min-h-10 border rounded-md px-2 py-1 gap-1 cursor-pointer bg-background",
                disabled && "opacity-50 pointer-events-none"
            )}
            onClick={onOpen}
        >
            {items.length === 0 ? (
                <span className="text-sm text-muted-foreground flex-1">{placeholder}</span>
            ) : (
                <>
                    {items.slice(0, visibleCount).map((item, i) => (
                        <span
                            key={i}
                            ref={(el) => { chipRefs.current[i] = el; }}
                            className="inline-flex items-center px-2 py-0.5 rounded-md bg-muted text-xs font-medium whitespace-nowrap"
                        >
                            {String(item[labelKey] ?? "")}
                        </span>
                    ))}
                    {overflowCount > 0 && (
                        <span
                            data-testid="overflow-badge"
                            className="inline-flex items-center px-2 py-0.5 rounded-md bg-primary/10 text-primary text-xs font-medium whitespace-nowrap"
                        >
                            +{overflowCount}
                        </span>
                    )}
                </>
            )}
            <div className="ml-auto flex items-center gap-0.5 shrink-0">
                {items.length > 0 && (
                    <button
                        type="button"
                        aria-label="Clear selection"
                        disabled={disabled}
                        onClick={(e) => { e.stopPropagation(); onClear(); }}
                        className="p-1 rounded text-muted-foreground hover:text-foreground"
                    >
                        <X className="size-3.5" />
                    </button>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Open table"
                    disabled={disabled}
                    className="size-7"
                >
                    <LayoutGrid className="size-3.5" />
                </Button>
            </div>
        </div>
    );
}

const SelectTableTrigger = React.forwardRef<HTMLDivElement, ISelectTableTriggerProps>(
    ({ value, multiple, labelKey, labelOverride, placeholder, disabled, onOpen, onClear }, ref) => {
        if (multiple) {
            const items = Array.isArray(value) ? value : [];
            return (
                <div ref={ref}>
                    <MultiTrigger
                        items={items}
                        labelKey={labelKey}
                        placeholder={placeholder}
                        disabled={disabled}
                        onOpen={onOpen}
                        onClear={onClear}
                    />
                </div>
            );
        }

        const row = value && !Array.isArray(value) && typeof value === "object" ? value : null;
        const label = labelOverride
            ?? (row ? String(row[labelKey] ?? "") : "");

        return (
            <div ref={ref}>
                <SingleTrigger
                    label={label}
                    placeholder={placeholder}
                    disabled={disabled}
                    hasValue={!!row || !!labelOverride || ((value != null || value !== undefined) && typeof value !== "object")}
                    onOpen={onOpen}
                    onClear={onClear}
                />
            </div>
        );
    }
);

SelectTableTrigger.displayName = "SelectTableTrigger";

export default SelectTableTrigger;
