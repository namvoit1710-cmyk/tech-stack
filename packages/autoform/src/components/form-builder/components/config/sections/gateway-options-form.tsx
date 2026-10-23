import { useEffect, useMemo, useState } from "react";
import { Input } from "@ldc/ui/components/input";
import { useOptionalBuilderContext } from "../../../../../contexts/builder.context";
import {
    buildGatewayOptionsServerConfig,
    GATEWAY_OPTIONS_DEFAULT_DATAPATH,
    GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH,
    GATEWAY_OPTIONS_DEFAULT_TOTALPATH,
    GATEWAY_OPTIONS_SERVICE,
    gatewayAppsOptionsSource,
    gatewayFunctionsOptionsSource,
    parseGatewayOptionsServerConfig,
} from "../../../../../lib/gateway-options";
import type { IGatewayOptionsConfig, IParamBinding } from "../../../../../lib/gateway-options";
import { DEFAULT_SEARCH_ARG_KEY, fetchOptionRows } from "../../../../../lib/gateway-invoke";
import type { IOptionsRequestConfig, IOptionsServiceHandler } from "../../../../../lib/gateway-invoke";
import { CollapsibleSection } from "../shared/collapsible-section";
import { GatewayContextValuesEditor } from "./gateway-context-values-editor";
import { GatewayPageBindingsEditor } from "./gateway-page-bindings-editor";
import { GatewaySelect } from "./gateway-select";
import { Field, Section } from "../shared/panel-primitives";

interface IGatewayFunctionParamLike {
    key: string;
    label?: string;
    location?: string;
}

interface IGatewayFunctionLike {
    name: string;
    input_params?: IGatewayFunctionParamLike[];
}

interface IGatewayAppLike {
    name: string;
}

const EMPTY_CONFIG: IGatewayOptionsConfig = { appName: "", functionName: "", functionId: "", dataPath: "" };


/**
 * Run one of the authoring pickers' own `serverOptions` configs. Identical
 * transport to the controls being authored — the pickers eat the feature they
 * configure, so a broken gateway path shows up here first.
 */
function useOptionRows<T>(
    service: IOptionsServiceHandler | undefined,
    source: IOptionsRequestConfig | null,
): T[] {
    const [rows, setRows] = useState<T[]>([]);
    const sourceKey = source ? JSON.stringify(source) : "";

    useEffect(() => {
        if (!service || !sourceKey) {
            setRows([]);
            return;
        }
        let cancelled = false;
        fetchOptionRows(service, JSON.parse(sourceKey) as IOptionsRequestConfig)
            .then((r) => { if (!cancelled) setRows(r as T[]); })
            .catch(() => { if (!cancelled) setRows([]); });
        return () => { cancelled = true; };
    }, [service, sourceKey]);

    return rows;
}

export interface GatewayOptionsFormProps {
    value: Record<string, unknown>;
    onChange: (next: Record<string, unknown>) => void;
    /** Combobox (typeahead) only — Select/RadioPopup have no search binding. */
    searchBinding: boolean;
    /** SelectTable (Phase B) only — the invoke response is a paged envelope, so this
     *  control also authors a Total path + a contextValues (form field → gateway arg)
     *  mapping, and defaults its Data path one level deeper than Phase A. */
    paged?: boolean;
}

/**
 * SA-2184 — the Gateway authoring UI (wireframe `sa-2184-2192-config-panel-wireframe.png`,
 * "A" = Phase A parts only): App/Function pickers, an optional search-param binding for
 * typeahead controls, per-param expose/lock bindings, and the response data-path — no
 * hand-typed invoke JSON. `mode`/`odataConfig` never appear here (Gateway has no client-side
 * default/odata sub-mode); those stay on the legacy REST `ServerOptionsForm`.
 *
 * The App/Function options are themselves gateway option sources, invoked through the same
 * transport as the control being authored (`gatewayAppsOptionsSource` /
 * `gatewayFunctionsOptionsSource`, the latter depending on the picked app). They need
 * `services.workflow` off the BuilderContext a hosting Builder/FormBuilder provides; without
 * one, the pickers render empty rather than throwing.
 */
export function GatewayOptionsForm({ value, onChange, searchBinding, paged = false }: GatewayOptionsFormProps) {
    const ctx = useOptionalBuilderContext();
    const workflowService = ctx?.services?.[GATEWAY_OPTIONS_SERVICE];

    const cfg = useMemo<IGatewayOptionsConfig>(
        () => parseGatewayOptionsServerConfig(value, { paged }) ?? EMPTY_CONFIG,
        [value, paged]
    );

    // Both pickers are gateway option sources; the Function one declares the App
    // picker as its dependency (picked app → args.app → upstream path param).
    const apps = useOptionRows<IGatewayAppLike>(workflowService, gatewayAppsOptionsSource());
    const functions = useOptionRows<IGatewayFunctionLike>(
        workflowService,
        gatewayFunctionsOptionsSource(cfg.appName)
    );

    const selectedFunction = functions.find((f) => f.name === cfg.functionName);
    const params = selectedFunction?.input_params ?? [];

    const emit = (patch: Partial<IGatewayOptionsConfig>) =>
        onChange(buildGatewayOptionsServerConfig({ ...cfg, ...patch }, { paged }));

    // A binding that is "lock" with no constant yet (or "expose" with no dependency use
    // downstream) serializes to nothing — buildGatewayOptionsServerConfig correctly drops
    // it (it would contribute nothing at runtime). That round-trip loss would otherwise
    // snap the mode selector straight back to its default the instant the author picks
    // "lock" but hasn't typed a value yet, so the constant input never gets a chance to
    // appear. Track the in-progress selection locally per function; it resets when the
    // function changes (a different param set).
    const [localBindings, setLocalBindings] = useState<Record<string, IParamBinding>>({});
    useEffect(() => { setLocalBindings({}); }, [cfg.functionName]);

    const bindingFor = (key: string): IParamBinding =>
        localBindings[key] ?? (cfg.bindings ?? []).find((b) => b.key === key) ?? { key, mode: "expose" };

    const setBinding = (key: string, patch: Partial<IParamBinding>) => {
        const next: IParamBinding = { ...bindingFor(key), ...patch };
        setLocalBindings((prev) => ({ ...prev, [key]: next }));
        const current = cfg.bindings ?? [];
        const nextBindings = current.some((b) => b.key === key)
            ? current.map((b) => (b.key === key ? next : b))
            : [...current, next];
        emit({ bindings: nextBindings });
    };

    return (
        <div data-source="gateway">
            <Section title="Gateway function" />
            <div className="grid grid-cols-2 gap-2 px-3 mb-2">
                <div className="flex flex-col gap-1">
                    <label className="text-[10px] font-medium text-muted-foreground">App *</label>
                    <GatewaySelect
                        ariaLabel="Gateway app"
                        value={cfg.appName}
                        emptyLabel="Select…"
                        options={apps.map((a) => ({ value: a.name, label: a.name }))}
                        onChange={(appName) => emit({ appName, functionName: "", bindings: [] })}
                    />
                </div>
                <div className="flex flex-col gap-1">
                    <label className="text-[10px] font-medium text-muted-foreground">Function *</label>
                    <GatewaySelect
                        ariaLabel="Gateway function"
                        value={cfg.functionName}
                        emptyLabel="Select…"
                        disabled={!cfg.appName}
                        options={functions.map((f) => ({ value: f.name, label: f.name }))}
                        onChange={(functionName) => emit({ functionName, bindings: [] })}
                    />
                </div>
            </div>

            {searchBinding && (
                <>
                    <Section title="Search binding" />
                    <Field label="Search param">
                        <GatewaySelect
                            ariaLabel="Search param"
                            value={cfg.searchArgKey ?? DEFAULT_SEARCH_ARG_KEY}
                            options={[
                                { value: DEFAULT_SEARCH_ARG_KEY, label: DEFAULT_SEARCH_ARG_KEY },
                                ...params
                                    .filter((p) => p.key !== DEFAULT_SEARCH_ARG_KEY)
                                    .map((p) => ({ value: p.key, label: p.key })),
                            ]}
                            onChange={(searchArgKey) => emit({ searchArgKey })}
                        />
                    </Field>
                    <p className="text-xs text-muted-foreground px-3 mb-2">
                        Typed term → args[searchArgKey]; pick from the function's query params. Default $search.
                    </p>
                </>
            )}

            {paged && (
                <GatewayPageBindingsEditor
                    value={cfg.pageArgKeys}
                    onChange={(pageArgKeys) => emit({ pageArgKeys })}
                    paramKeys={params.map((p) => p.key)}
                />
            )}

            {params.length > 0 && (
                <>
                    <Section title="Parameter bindings" />
                    <div className="px-3 mb-2 flex flex-col gap-2">
                        {params.map((p) => {
                            const b = bindingFor(p.key);
                            return (
                                <div key={p.key} className="grid grid-cols-3 gap-2 items-center">
                                    {/* eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing -- an empty-string label must also fall back to the key, not render blank */}
                                    <span className="text-xs truncate">{p.label || p.key}</span>
                                    <GatewaySelect
                                        ariaLabel={`${p.key} binding mode`}
                                        className="h-7! w-full text-xs!"
                                        value={b.mode}
                                        options={[
                                            { value: "expose", label: "expose" },
                                            { value: "lock", label: "lock" },
                                        ]}
                                        onChange={(mode) => setBinding(p.key, { mode: mode as IParamBinding["mode"] })}
                                    />
                                    {b.mode === "lock" ? (
                                        <Input
                                            aria-label={`${p.key} constant`}
                                            className="h-7 text-xs!"
                                            placeholder="constant value"
                                            value={b.constant ?? ""}
                                            onChange={(e) => setBinding(p.key, { constant: e.target.value })}
                                        />
                                    ) : (
                                        <span className="text-xs text-muted-foreground truncate">dep field: {p.key}</span>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <p className="text-xs text-muted-foreground px-3 mb-2">
                        expose = filled at runtime (dep field key must match param key) · lock = baked constant.
                    </p>
                </>
            )}

            {paged && (
                <GatewayContextValuesEditor
                    value={cfg.contextValues}
                    onChange={(contextValues) => emit({ contextValues })}
                />
            )}

            <CollapsibleSection label="Advanced — response mapping">
                <Field label="Data path">
                    <Input
                        aria-label="Data path"
                        className="h-8 text-xs!"
                        placeholder={paged ? GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH : GATEWAY_OPTIONS_DEFAULT_DATAPATH}
                        value={cfg.dataPath}
                        onChange={(e) => emit({ dataPath: e.target.value })}
                    />
                </Field>
                {paged && (
                    <Field label="Total path">
                        <Input
                            aria-label="Total path"
                            className="h-8 text-xs!"
                            placeholder={GATEWAY_OPTIONS_DEFAULT_TOTALPATH}
                            value={cfg.totalPath ?? ""}
                            onChange={(e) => emit({ totalPath: e.target.value })}
                        />
                    </Field>
                )}
            </CollapsibleSection>
        </div>
    );
}

export default GatewayOptionsForm;
