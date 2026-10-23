import type React from "react";

/**
 * The three layout primitives every config form is built from. Moved verbatim out of
 * `config-panel.tsx` — the markup and the class strings are unchanged, which is what
 * makes the extraction behaviour-preserving.
 *
 * `Section` had a second, byte-identical copy inside `forms/odata-config-form.tsx`.
 * That copy is deleted: one definition, not two.
 */
export function PanelHeader({ children }: { children: React.ReactNode }) {
    return (
        <div className="px-3 py-2.5 border-b border-border flex-shrink-0">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                {children}
            </p>
        </div>
    );
}

export function Section({ title }: { title: string }) {
    return (
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 mt-3 mb-1.5 px-3 border-t border-border pt-3">
            {title}
        </p>
    );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-col gap-1 px-3 mb-2">
            <label className="text-[10px] font-medium text-muted-foreground">{label}</label>
            {children}
        </div>
    );
}
