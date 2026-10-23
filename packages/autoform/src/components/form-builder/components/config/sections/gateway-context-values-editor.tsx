import { useState } from "react";
import { Input } from "@ldc/ui/components/input";
import type { IContextValueBinding } from "../../../../../lib/gateway-options";
import { Section } from "../shared/panel-primitives";

export interface GatewayContextValuesEditorProps {
    value: IContextValueBinding[] | undefined;
    onChange: (next: IContextValueBinding[]) => void;
}

/**
 * SelectTable (Phase B) only — maps a gateway arg key to a form field; the resolved
 * value is watched and sent as one JSON-encoded queryParams.contextValues arg.
 *
 * A row with an empty apiKey (freshly added, or mid-edit) encodes to nothing —
 * buildGatewayOptionsServerConfig correctly drops it (it would contribute nothing
 * at runtime). That round-trip loss would otherwise make the row vanish the
 * instant it's added, before the author can type into it — tracked locally here,
 * same fix as GatewayOptionsForm's param-bindings case.
 */
export function GatewayContextValuesEditor({ value, onChange }: GatewayContextValuesEditorProps) {
    const [local, setLocal] = useState<IContextValueBinding[] | null>(null);
    const rows = local ?? value ?? [];

    const emit = (next: IContextValueBinding[]) => {
        setLocal(next);
        onChange(next);
    };
    const setRow = (index: number, patch: Partial<IContextValueBinding>) => {
        const next = rows.map((row, i) => (i === index ? { ...row, ...patch } : row));
        emit(next);
    };
    const addRow = () => emit([...rows, { apiKey: "", formField: "" }]);
    const removeRow = (index: number) => emit(rows.filter((_, i) => i !== index));

    return (
        <>
            <Section title="Context values" />
            <div className="px-3 mb-2 flex flex-col gap-2">
                {rows.map((cv, i) => (
                    <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
                        <Input
                            aria-label={`Context value ${i} API key`}
                            className="h-7 text-xs!"
                            placeholder="gateway arg key"
                            value={cv.apiKey}
                            onChange={(e) => setRow(i, { apiKey: e.target.value })}
                        />
                        <Input
                            aria-label={`Context value ${i} form field`}
                            className="h-7 text-xs!"
                            placeholder="form field key"
                            value={cv.formField}
                            onChange={(e) => setRow(i, { formField: e.target.value })}
                        />
                        <button
                            type="button"
                            aria-label={`Remove context value ${i}`}
                            className="h-7 text-xs text-muted-foreground hover:text-foreground px-2"
                            onClick={() => removeRow(i)}
                        >
                            ✕
                        </button>
                    </div>
                ))}
                <button
                    type="button"
                    className="h-7 text-xs text-left text-muted-foreground hover:text-foreground"
                    onClick={addRow}
                >
                    + Add context value
                </button>
            </div>
            <p className="text-xs text-muted-foreground px-3 mb-2">
                Maps a gateway arg key to a form field; its resolved value is watched and
                sent as one JSON-encoded queryParams.contextValues arg.
            </p>
        </>
    );
}

export default GatewayContextValuesEditor;
