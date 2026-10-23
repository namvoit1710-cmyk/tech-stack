import { Plus, X } from "lucide-react";

import { Input } from "@ldc/ui/components/input";

import type { ISelectOption } from "../../../../field-control/select-control";
import { Section } from "../shared/panel-primitives";

export interface OptionsEditorProps {
    options: ISelectOption[];
    /** Receives the whole next list; the caller flushes it to the node. */
    onChange: (next: ISelectOption[]) => void;
}

/**
 * The Options list for the controls that take one (`SelectControl`, radio, …).
 *
 * Moved verbatim out of `config-panel.tsx` (the Options block plus `addOption` /
 * `updateOption` / `removeOption`). The list was ALREADY node-driven there — it read
 * `fieldConfig.controlProps.options` on every render and wrote straight through
 * `updateNode` — so it has no local state of its own here either, and every edit still
 * flushes immediately.
 */
export function OptionsEditor({ options, onChange }: OptionsEditorProps) {
    const update = (i: number, k: keyof ISelectOption, v: string) => {
        const next = [...options];
        const current = next[i];
        if (!current) return;
        next[i] = { ...current, [k]: v };
        onChange(next);
    };

    const remove = (i: number) => {
        const next = [...options];
        next.splice(i, 1);
        onChange(next);
    };

    const add = () =>
        onChange([
            ...options,
            { id: `opt_${options.length + 1}`, value: `Option ${options.length + 1}` },
        ]);

    return (
        <>
            <Section title="Options" />
            <div className="px-3 space-y-1 mb-2">
                {options.map((opt, i) => (
                    <div key={i} className="flex items-center gap-1">
                        <Input
                            value={opt.id}
                            onChange={(e) => update(i, "id", e.target.value)}
                            placeholder="id"
                            className="h-8 text-xs! flex-1"
                        />
                        <Input
                            value={opt.value}
                            onChange={(e) => update(i, "value", e.target.value)}
                            placeholder="label"
                            className="h-8 text-xs! flex-1"
                        />
                        <button
                            onClick={() => remove(i)}
                            className="p-1 text-muted-foreground hover:text-destructive"
                        >
                            <X className="w-3 h-3" />
                        </button>
                    </div>
                ))}
                <button
                    onClick={add}
                    className="flex items-center gap-1 text-xs! text-primary hover:underline mt-1"
                >
                    <Plus className="w-3 h-3" /> Add option
                </button>
            </div>
        </>
    );
}

export default OptionsEditor;
