import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";
import { useEffect, useMemo, useState } from "react";
import { CONTROL_EVENTS } from "../../../../../libs/action-engine/script/events";
import { CTX_HINTS } from "../../../../../libs/action-engine/script/action-script-ctx.dts";
import type { DesignerNode } from "../../../types";
import { CollapsibleSection } from "../shared/collapsible-section";
import { EventCodeEditor } from "./event-code-editor";

export interface EventsEditorProps {
    node: DesignerNode;
    setScript: (event: string, source: string) => void;
}

const scaffold = (event: string) =>
    `/** @param {ActionScriptCtx} ctx */\nasync function ${event}(ctx) {\n  \n}\n`;

const hasCode = (script: Record<string, string> | undefined, event: string) =>
    !!script?.[event] && script[event].trim() !== "";

export function EventsEditor({ node, setScript }: EventsEditorProps) {
    const control = node.fieldConfig.fieldControl ?? "";
    const events = CONTROL_EVENTS[control] ?? [];
    const script = node.fieldConfig.script;

    const codedCount = useMemo(() => events.filter((e) => hasCode(script, e)).length, [events, script]);
    const [selected, setSelected] = useState<string>(() => events.find((e) => hasCode(script, e)) ?? events[0] ?? "");

    useEffect(() => {
        setSelected(events.find((e) => hasCode(script, e)) ?? events[0] ?? "");
        // re-seed the selected event when a different node is inspected
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [node._id]);

    if (events.length === 0) return null;

    const current = script?.[selected] ?? "";

    const insertHint = (snippet: string) => {
        const base = current || scaffold(selected);
        // Insert before the last closing brace so it lands inside the function body.
        const idx = base.lastIndexOf("}");
        const next = idx >= 0 ? `${base.slice(0, idx)}  ${snippet};\n${base.slice(idx)}` : `${base}\n${snippet};`;
        setScript(selected, next);
    };

    return (
        <CollapsibleSection label="Events" count={codedCount} countTestId="events-count">
            <div className="flex flex-col gap-2 px-3">
                <span className="text-xs font-medium text-muted-foreground">Event</span>
                <ToggleGroup
                    type="single"
                    variant="outline"
                    size="sm"
                    value={selected}
                    onValueChange={(v) => v && setSelected(v)}
                    className="grid grid-flow-col auto-cols-fr gap-1"
                >
                    {events.map((e) => (
                        <ToggleGroupItem key={e} value={e} aria-label={e} className="text-xs">
                            {e}
                            {hasCode(script, e) && <span aria-hidden className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-green-500" />}
                        </ToggleGroupItem>
                    ))}
                </ToggleGroup>
                <p className="text-xs text-muted-foreground">
                    Dot = has code. Empty = not yet written. Events come from the control type.
                </p>

                <div className="flex items-center justify-between rounded-t-md bg-muted px-2 py-1">
                    <code className="text-xs">{`async function ${selected}(ctx)`}</code>
                    <span className="rounded bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">browser · author-trusted</span>
                </div>

                <EventCodeEditor
                    value={current || scaffold(selected)}
                    onChange={(v) => setScript(selected, v)}
                />

                <div className="flex flex-wrap gap-1">
                    {CTX_HINTS.map((h) => (
                        <button
                            key={h.label}
                            type="button"
                            className="rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                            onClick={() => insertHint(h.snippet)}
                        >
                            {h.label}
                        </button>
                    ))}
                </div>

                <p className="text-[11px] text-muted-foreground">
                    Stored at <code>fieldConfig.script.{selected}</code> — compiled with <code>(0,eval)</code> (same trust posture as evalExpr) · errors → <code>ctx.toast</code>.
                </p>
            </div>
        </CollapsibleSection>
    );
}
