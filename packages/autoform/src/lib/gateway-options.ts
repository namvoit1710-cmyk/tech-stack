/**
 * SA-2184 — pure derivation of a Choice control's `serverOptions` from a chosen
 * Gateway App + Function. Moved out of the workflow-management app (a package
 * cannot import app code) so the autoform Form Builder's Gateway authoring UI
 * can build/parse this shape directly; `gateway-binding.ts` (workflow-management)
 * now imports it from here instead of defining it locally.
 *
 * No React here — pure functions only.
 */
import {
    DEFAULT_PAGE_ARG_KEYS,
    GATEWAY_INVOKE_DATAPATH,
    GATEWAY_INVOKE_ENDPOINT,
    GATEWAY_INVOKE_PAGED_DATAPATH,
    GATEWAY_INVOKE_SERVICE,
    GATEWAY_INVOKE_TOTALPATH,
} from "./gateway-invoke";
import type { IPageArgKeys } from "./gateway-invoke";

export const GATEWAY_OPTIONS_SERVICE = GATEWAY_INVOKE_SERVICE;
export const GATEWAY_OPTIONS_ENDPOINT = GATEWAY_INVOKE_ENDPOINT;
export const GATEWAY_OPTIONS_DEFAULT_DATAPATH = GATEWAY_INVOKE_DATAPATH;
// SA-2192 (Phase B) — SelectTable's invoke response is a PAGED envelope; see
// gateway-invoke.ts for why its data/total paths sit one level deeper.
export const GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH = GATEWAY_INVOKE_PAGED_DATAPATH;
export const GATEWAY_OPTIONS_DEFAULT_TOTALPATH = GATEWAY_INVOKE_TOTALPATH;

// Both worker-type names have been deployed at different times (WORKER_TYPE=gateway
// today; gateway-worker before) — parse must recognise a serverOptions saved under
// either, so opening an old definition still detects the gateway source.
const GATEWAY_WORKER_TYPE_ALIASES = ["gateway", "gateway-worker"] as const;
const invokeEndpointFor = (workerType: string): string =>
    `workers/${workerType}/functions/invoke_app_function/invoke`;

// Every definition saved before the fix carries a "result.*" path, which resolves to
// undefined and yields a silently empty dropdown — it has never once worked. Healing
// it on build therefore cannot break behaviour that existed; it can only start working.
const withEnvelopePrefix = (dataPath: string, fallback: string): string => {
    const path = dataPath.trim();
    if (!path) return fallback;
    return path.startsWith("result.") ? `data.${path}` : path;
};

/** SelectTable-only — one gateway-arg-key ↔ form-field binding, sent (all together) as
 *  a single JSON-encoded `queryParams.contextValues` string, decoded by the hook. */
export interface IContextValueBinding {
    apiKey: string;
    formField: string;
}
// ─── The Form Builder's own pickers are gateway option sources too ───────────
// The App and Function dropdowns in the Gateway authoring UI are not bespoke
// fetches: they are `serverOptions` configs of exactly the shape this module
// emits, invoked through the same transport. The control plane is registered as
// a gateway app (`control-plane`) exposing two list functions; the Function
// picker declares the App picker as its dependency, so the picked app rides
// `args.app` and the gateway-worker places it into `/gateway/apps/{app}/functions`
// by the param's declared `location: path`.
//
// Consequence to document for operators: an environment where the `control-plane`
// gateway app has not been registered yet shows empty pickers. That is the
// bootstrap cost of sourcing the authoring UI through the gateway itself.

/** Gateway app name that fronts the control plane's own registry endpoints. */
export const GATEWAY_REGISTRY_APP = "control-plane";
export const GATEWAY_REGISTRY_LIST_APPS_FN = "list_gateway_apps";
export const GATEWAY_REGISTRY_LIST_FUNCTIONS_FN = "list_gateway_functions";

/** The registry endpoints answer with the control plane's `{status,data:{items}}`
 *  envelope, which the invoke response wraps again → rows at `…body.data.items`. */
export const GATEWAY_REGISTRY_DATAPATH = `${GATEWAY_INVOKE_DATAPATH}.data.items`;

const registrySource = (fn: string, args: Record<string, unknown> = {}) => ({
    service: GATEWAY_OPTIONS_SERVICE,
    endpoint: GATEWAY_OPTIONS_ENDPOINT,
    method: "POST" as const,
    body: { params: { app: GATEWAY_REGISTRY_APP, function: fn, args } },
    responseMapping: { dataPath: GATEWAY_REGISTRY_DATAPATH },
});

/** Option source for the App picker — no dependencies. */
export const gatewayAppsOptionsSource = () => registrySource(GATEWAY_REGISTRY_LIST_APPS_FN);

/** Option source for the Function picker — depends on the picked app, which
 *  rides `args.app` and lands in the upstream path. Returns `null` until an app
 *  is picked, mirroring the runtime rule that a source with unmet dependencies
 *  fetches nothing. */
export const gatewayFunctionsOptionsSource = (appName: string) =>
    appName ? registrySource(GATEWAY_REGISTRY_LIST_FUNCTIONS_FN, { app: appName }) : null;

/** Per gateway-function param: surface it to the end user as a bound dependency
 *  field, or bake a constant. Field key on the form MUST equal the param key
 *  for `expose` bindings — no separate remap in Phase A (SA-2184). */
export interface IParamBinding {
    key: string;
    mode: "expose" | "lock";
    constant?: string;
}

/** Friendly (form-state) config the field editor edits for a gateway option source. */
export interface IGatewayOptionsConfig {
    appName: string;
    functionName: string;
    /** Legacy: still parsed off old saved configs, but no longer sent. */
    functionId: string;
    /** Path under the invoke response to the options array (default `data.result.body`,
     *  or `data.result.body.data` for a paged/SelectTable source). */
    dataPath: string;
    /** Combobox typeahead only — typed term → args[searchArgKey]. Default `"$search"`. */
    searchArgKey?: string;
    /** Per-param expose/lock decisions; `expose` params become `dependencies[]`. */
    bindings?: IParamBinding[];
    /** SelectTable (paged) only — path under the invoke response to the row count
     *  (default `data.result.body.total_count`). */
    totalPath?: string;
    /** SelectTable only — form fields resolved into the gateway's `contextValues` arg. */
    contextValues?: IContextValueBinding[];
    /** SelectTable (Phase B) only — the upstream's own names for the page args.
     *  Defaults to the OData spellings; `""` means the arg is not sent at all. */
    pageArgKeys?: IPageArgKeys;
}

/** Build the autoform `controlProps.serverOptions` that invokes a gateway function.
 *  The (app, function) pair goes in the body as the authoritative selector, so the
 *  config is portable across environments. Exposed params become `dependencies[]`
 *  (refetch on change); locked params are baked constants under `body.params.args`.
 *  `paged` (SelectTable / Phase B only) selects the deeper default dataPath and
 *  turns on `totalPath` + `contextValues` (queryParams-carried, not gateway args —
 *  they resolve per-request from watched form fields, unlike locked/exposed params). */
export function buildGatewayOptionsServerConfig(
    cfg: IGatewayOptionsConfig,
    opts: { paged?: boolean } = {},
): Record<string, unknown> {
    const lockedArgs: Record<string, unknown> = {};
    const dependencies: string[] = [];
    for (const b of cfg.bindings ?? []) {
        if (b.mode === "lock") {
            if (b.constant !== undefined && b.constant !== "") lockedArgs[b.key] = b.constant;
        } else {
            dependencies.push(b.key);
        }
    }

    const defaultDataPath = opts.paged ? GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH : GATEWAY_OPTIONS_DEFAULT_DATAPATH;
    const responseMapping: Record<string, unknown> = { dataPath: withEnvelopePrefix(cfg.dataPath, defaultDataPath) };
    if (opts.paged && cfg.totalPath) responseMapping.totalPath = cfg.totalPath;

    const out: Record<string, unknown> = {
        service: GATEWAY_OPTIONS_SERVICE,
        endpoint: GATEWAY_OPTIONS_ENDPOINT,
        method: "POST",
        body: { params: { app: cfg.appName, function: cfg.functionName, args: lockedArgs } },
        responseMapping,
    };
    if (dependencies.length) out.dependencies = dependencies;
    if (cfg.searchArgKey) out.searchArgKey = cfg.searchArgKey;

    // Only emit page-arg names that actually differ from the OData defaults, so a
    // config the author never touched stays byte-identical to what shipped before.
    if (opts.paged && cfg.pageArgKeys) {
        const overrides = Object.fromEntries(
            (["top", "skip", "count"] as const)
                .filter((k) => cfg.pageArgKeys?.[k] !== undefined && cfg.pageArgKeys[k] !== DEFAULT_PAGE_ARG_KEYS[k])
                .map((k) => [k, cfg.pageArgKeys![k]])
        );
        if (Object.keys(overrides).length) out.pageArgKeys = overrides;
    }

    if (opts.paged && cfg.contextValues?.length) {
        const mapping: Record<string, string> = {};
        for (const b of cfg.contextValues) {
            if (b.apiKey) mapping[b.apiKey] = b.formField;
        }
        if (Object.keys(mapping).length) {
            out.queryParams = { contextValues: encodeURIComponent(JSON.stringify(mapping)) };
        }
    }
    return out;
}

/** Detect + reverse a gateway-invoke serverOptions back to the friendly config.
 *  Accepts the id-addressed shape saved by earlier builds, under either worker-type
 *  alias, so opening an old definition still populates the pickers. */
export function parseGatewayOptionsServerConfig(
    so: unknown,
    opts: { paged?: boolean } = {},
): IGatewayOptionsConfig | null {
    const cfg = so as {
        method?: string; endpoint?: string;
        body?: { params?: { app?: string; function?: string; function_id?: string; args?: Record<string, unknown> } };
        responseMapping?: { dataPath?: string; totalPath?: string };
        dependencies?: string[];
        searchArgKey?: string;
        pageArgKeys?: IPageArgKeys;
        queryParams?: { contextValues?: string };
        _gateway?: { app?: string; function?: string };   // legacy UI-only hint
    } | null;
    if (cfg?.method !== "POST") return null;
    const isInvokeEndpoint = GATEWAY_WORKER_TYPE_ALIASES.some(
        (t) => cfg.endpoint === invokeEndpointFor(t)
    );
    if (!isInvokeEndpoint) return null;

    const params = cfg.body?.params ?? {};
    const args = (params.args ?? {});
    const deps = new Set(cfg.dependencies ?? []);
    const bindings: IParamBinding[] = [
        ...Object.entries(args).map(([key, val]) => ({
            key,
            mode: "lock" as const,
            constant: typeof val === "string" ? val : JSON.stringify(val),
        })),
        ...[...deps].filter((key) => !(key in args)).map((key) => ({ key, mode: "expose" as const })),
    ];

    const defaultDataPath = opts.paged ? GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH : GATEWAY_OPTIONS_DEFAULT_DATAPATH;
    const out: IGatewayOptionsConfig = {
        appName: String(params.app ?? cfg._gateway?.app ?? ""),
        functionName: String(params.function ?? cfg._gateway?.function ?? ""),
        functionId: String(params.function_id ?? ""),
        dataPath: String(cfg.responseMapping?.dataPath ?? defaultDataPath),
    };
    if (bindings.length) out.bindings = bindings;
    if (cfg.searchArgKey) out.searchArgKey = cfg.searchArgKey;
    if (cfg.pageArgKeys) out.pageArgKeys = cfg.pageArgKeys;

    if (opts.paged) {
        if (cfg.responseMapping?.totalPath) out.totalPath = cfg.responseMapping.totalPath;
        if (cfg.queryParams?.contextValues) {
            try {
                const mapping = JSON.parse(decodeURIComponent(cfg.queryParams.contextValues)) as Record<string, string>;
                const contextValues = Object.entries(mapping).map(([apiKey, formField]) => ({ apiKey, formField }));
                if (contextValues.length) out.contextValues = contextValues;
            } catch {
                // Malformed contextValues on a saved config — leave it unset rather than throw.
            }
        }
    }
    return out;
}
