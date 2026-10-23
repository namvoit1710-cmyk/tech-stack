// Reusable expression typeahead: typing `{{` opens a grouped {{$...}} dropdown fed by a
// `suggestions` prop. Extracted from the canvas node-config control so both the workflow
// canvas (suggestions from node-detail context) and the node-definition builder (suggestions
// derived from the node's own input schema / worker output) can share one implementation.
//
// The dropdown is rendered through a portal with fixed positioning so it is never clipped by
// an ancestor's `overflow` (the builder's scrollable tabs/cards previously hid it).
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { cn } from "@ldc/ui";
import { Input } from "@ldc/ui/components/input";
import { Textarea } from "@ldc/ui/components/textarea";
import { forwardRef, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

type EditableEl = HTMLInputElement | HTMLTextAreaElement;

export interface IActiveToken {
    start: number; // index of the opening "{{"
    query: string; // lowercased text typed after "{{" (and optional "$")
}

// Returns the active expression token iff the caret sits inside an unclosed "{{ … }}".
export const getActiveToken = (text: string, caret: number): IActiveToken | null => {
    const open = text.lastIndexOf("{{", Math.max(0, caret - 1));
    if (open === -1) return null;
    const between = text.slice(open, caret);
    if (between.includes("}}")) return null;
    return { start: open, query: between.replace(/^\{\{\s*\$?/, "").toLowerCase() };
};

interface IPos { top: number; left: number; width: number }

export interface ExpressionAutocompleteInputProps {
    value: string;
    onChange: (next: string) => void;
    suggestions: IExprSuggestion[];
    multiline?: boolean;
    placeholder?: string;
    className?: string;
    rows?: number;
    disabled?: boolean;
    error?: boolean;
    onBlur?: (e: React.FocusEvent) => void;
    "aria-label"?: string;
    name?: string;
}

export const ExpressionAutocompleteInput = forwardRef<EditableEl, ExpressionAutocompleteInputProps>((props, forwardedRef) => {
    const { value, onChange, suggestions, multiline, className, rows, disabled, error, onBlur, ...rest } = props;

    const innerRef = useRef<EditableEl | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [open, setOpen] = useState(false);
    const [token, setToken] = useState<IActiveToken | null>(null);
    const [focused, setFocused] = useState(-1);
    const [pos, setPos] = useState<IPos | null>(null);

    const text = typeof value === "string" ? value : value == null ? "" : String(value);

    const setRefs = useCallback((el: EditableEl | null) => {
        innerRef.current = el;
        if (typeof forwardedRef === "function") forwardedRef(el);
        else if (forwardedRef) (forwardedRef as React.MutableRefObject<EditableEl | null>).current = el;
    }, [forwardedRef]);

    const filtered = useMemo(() => {
        if (!token) return [];
        const q = token.query;
        const list = !q
            ? suggestions
            : suggestions.filter((s) => s.value.toLowerCase().includes(q) || s.label.toLowerCase().includes(q));
        return list.slice(0, 50);
    }, [suggestions, token]);

    const grouped = useMemo(() => {
        const map = new Map<string, IExprSuggestion[]>();
        for (const s of filtered) {
            const arr = map.get(s.group) ?? [];
            arr.push(s);
            map.set(s.group, arr);
        }
        return Array.from(map.entries());
    }, [filtered]);

    const updatePos = useCallback(() => {
        const el = innerRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        setPos({ top: r.bottom + 4, left: r.left, width: r.width });
    }, []);

    const isOpen = open && !disabled && filtered.length > 0;

    // Reposition the portaled dropdown while it is open (scroll inside any ancestor / resize).
    useEffect(() => {
        if (!isOpen) return;
        updatePos();
        const onScroll = () => updatePos();
        window.addEventListener("scroll", onScroll, true);
        window.addEventListener("resize", onScroll);
        return () => {
            window.removeEventListener("scroll", onScroll, true);
            window.removeEventListener("resize", onScroll);
        };
    }, [isOpen, updatePos]);

    const insert = useCallback((s: IExprSuggestion) => {
        const el = innerRef.current;
        const caret = el?.selectionStart ?? text.length;
        const start = token?.start ?? caret;
        const next = text.slice(0, start) + s.value + text.slice(caret);
        onChange(next);
        setOpen(false);
        setToken(null);
        setFocused(-1);
        requestAnimationFrame(() => {
            const p = start + s.value.length;
            el?.setSelectionRange?.(p, p);
            el?.focus();
        });
    }, [text, token, onChange]);

    const handleChange = (e: React.ChangeEvent<EditableEl>) => {
        const next = e.target.value;
        onChange(next);
        const tk = getActiveToken(next, e.target.selectionStart ?? next.length);
        setToken(tk);
        setOpen(!!tk);
        setFocused(-1);
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (!isOpen) return;
        if (e.key === "ArrowDown") { e.preventDefault(); setFocused((p) => Math.min(p + 1, filtered.length - 1)); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setFocused((p) => Math.max(p - 1, 0)); }
        else if (e.key === "Enter" && focused >= 0) { e.preventDefault(); const s = filtered[focused]; if (s) insert(s); }
        else if (e.key === "Escape") { setOpen(false); }
    };

    const handleBlur = (e: React.FocusEvent) => {
        setOpen(false);
        onBlur?.(e);
    };

    const shared = {
        ...rest,
        value: text,
        disabled,
        "aria-invalid": !!error,
        onChange: handleChange,
        onKeyDown,
        onBlur: handleBlur,
        onFocus: () => { const tk = getActiveToken(text, innerRef.current?.selectionStart ?? text.length); if (tk) { setToken(tk); setOpen(true); } },
        className: cn("font-mono", className),
    };

    let flatIdx = -1;

    return (
        <div className="relative" ref={containerRef}>
            {multiline
                ? <Textarea {...shared} rows={rows} ref={setRefs as React.Ref<HTMLTextAreaElement>} />
                : <Input {...shared} ref={setRefs as React.Ref<HTMLInputElement>} />}

            {isOpen && pos && typeof document !== "undefined" && createPortal(
                <div
                    // mousedown (not click) so the input doesn't blur before insert fires
                    onMouseDown={(e) => e.preventDefault()}
                    style={{ position: "fixed", top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
                    className="bg-popover border rounded-md shadow-lg max-h-56 overflow-y-auto"
                >
                    {grouped.map(([group, items]) => (
                        <div key={group}>
                            <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground bg-muted/50 sticky top-0">
                                {group}
                            </div>
                            {items.map((s) => {
                                flatIdx += 1;
                                const isFocused = flatIdx === focused;
                                return (
                                    <button
                                        key={s.value}
                                        type="button"
                                        className={cn(
                                            "w-full text-left px-3 py-1.5 text-sm hover:bg-accent flex flex-col",
                                            isFocused && "bg-accent"
                                        )}
                                        onMouseDown={(e) => { e.preventDefault(); insert(s); }}
                                    >
                                        <span className="font-mono text-xs text-primary">{s.value}</span>
                                        {s.description && <span className="text-xs text-muted-foreground">{s.description}</span>}
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                </div>,
                document.body
            )}
        </div>
    );
});
ExpressionAutocompleteInput.displayName = "ExpressionAutocompleteInput";

export default ExpressionAutocompleteInput;
