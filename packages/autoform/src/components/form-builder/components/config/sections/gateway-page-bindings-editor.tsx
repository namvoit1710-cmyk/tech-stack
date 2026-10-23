import { DEFAULT_PAGE_ARG_KEYS } from "../../../../../lib/gateway-invoke";
import type { IPageArgKeys } from "../../../../../lib/gateway-invoke";
import { GatewaySelect } from "./gateway-select";
import { Section } from "../shared/panel-primitives";

/** The three page args an author can bind, with their OData default spellings. */
const ROWS = [
    { slot: "top" as const, label: "Page size", fallback: DEFAULT_PAGE_ARG_KEYS.top },
    { slot: "skip" as const, label: "Offset", fallback: DEFAULT_PAGE_ARG_KEYS.skip },
    { slot: "count" as const, label: "Total count", fallback: DEFAULT_PAGE_ARG_KEYS.count },
];

export interface GatewayPageBindingsEditorProps {
    value: IPageArgKeys | undefined;
    onChange: (next: IPageArgKeys) => void;
    /** Declared param keys of the picked function — the only names the gateway forwards. */
    paramKeys: string[];
}

/**
 * SelectTable (Phase B) only — bind the page args to the UPSTREAM's own parameter names.
 *
 * The gateway forwards only args declared in the function's `input_params`, under those
 * exact keys; there is no rename step. So `$top`/`$skip` page nothing against an upstream
 * that calls them `top`/`skip` — and it fails silently, returning the same rows for every
 * page. The warning below is what makes that visible at authoring time instead.
 */
export function GatewayPageBindingsEditor({ value, onChange, paramKeys }: GatewayPageBindingsEditorProps) {
    return (
        <>
            <Section title="Page bindings" />
            <div className="px-3 mb-2 flex flex-col gap-2">
                {ROWS.map(({ slot, label, fallback }) => {
                    const current = value?.[slot] ?? fallback;
                    // Only judge a name once the function's params are actually known —
                    // while the function list is still loading `paramKeys` is empty, and
                    // warning then flags every correctly-bound name as unknown.
                    const isUnknown = paramKeys.length > 0 && current !== "" && !paramKeys.includes(current);
                    return (
                        <div key={slot} className="grid grid-cols-2 gap-2 items-center">
                            <span className="text-xs truncate">{label}</span>
                            <GatewaySelect
                                ariaLabel={`${slot} param`}
                                className="h-7! w-full text-xs!"
                                value={current}
                                emptyLabel="— don't send —"
                                options={[
                                    { value: fallback, label: fallback },
                                    ...paramKeys.filter((k) => k !== fallback).map((k) => ({ value: k, label: k })),
                                ]}
                                onChange={(next) => onChange({ ...value, [slot]: next })}
                            />
                            {isUnknown && (
                                <p className="col-span-2 text-xs text-amber-600">
                                    {`"${current}" is not one of this function's input_params — the gateway drops undeclared args, so this page arg never reaches the upstream.`}
                                </p>
                            )}
                        </div>
                    );
                })}
            </div>
            <p className="text-xs text-muted-foreground px-3 mb-2">
                The gateway forwards only declared params, under their own names — there is no
                rename step. Bind these to the upstream&apos;s paging params; the $-names are
                OData defaults and page nothing on an upstream that uses other names.
            </p>
        </>
    );
}

export default GatewayPageBindingsEditor;
