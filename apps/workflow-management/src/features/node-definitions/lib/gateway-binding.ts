/**
 * Pure derivation of a "gateway action" node-definition from a chosen gateway
 * function. A node whose base worker is the gateway-worker invokes a function by
 * dropping an `invoke_app_function` marker in its input; the control plane fires
 * it during input resolution (task_handler._resolve_function_calls) and splices
 * the response back as the node's output.
 *
 * No React here — see gateway-binding.test.ts for the guarantees.
 */
import type {
    IGatewayFunction,
    IGatewayFunctionParam,
    IGatewayOutputField,
} from "@/features/gateway-admin/types/gateway";
import {
    buildGatewayOptionsServerConfig,
    GATEWAY_OPTIONS_DEFAULT_DATAPATH,
    GATEWAY_OPTIONS_ENDPOINT,
    GATEWAY_OPTIONS_SERVICE,
    parseGatewayOptionsServerConfig,
    type IGatewayOptionsConfig,
    type IParamBinding,
} from "@ldc/autoform";
import type { IKeyValuePair, ISchemaFieldItem } from "./converters";

// SA-2184 — the gateway-invoke serverOptions helpers (buildGatewayOptionsServerConfig /
// parseGatewayOptionsServerConfig / IGatewayOptionsConfig / GATEWAY_OPTIONS_* + the
// IParamBinding concept) now live in the autoform package (a package cannot import app
// code, and the Form Builder's own Gateway authoring UI needs them too). Re-exported here
// so every existing importer in this app keeps working unchanged.
export {
    buildGatewayOptionsServerConfig,
    GATEWAY_OPTIONS_DEFAULT_DATAPATH,
    GATEWAY_OPTIONS_ENDPOINT,
    GATEWAY_OPTIONS_SERVICE,
    parseGatewayOptionsServerConfig,
};
export type { IGatewayOptionsConfig, IParamBinding };

// The gateway-worker registers three functions; `invoke_app_function` is the
// high-level one that resolves internal OR external apps via the control plane.
// call_internal/call_external are low-level. It addresses a function either by
// `function_id` or by (`app`, `function`) — we use the NAME pair: ids are minted
// per-environment, so a definition pinning one cannot be promoted dev → qa → prod.
export const GATEWAY_WORKER_FUNCTION = "invoke_app_function";

// The marker key under which the gateway response lands in the node's raw output.
// The worker returns {status_code, headers, body}; `body` is the actual API result.
export const GATEWAY_RESULT_KEY = "gateway_result";

// The worker's registered type, i.e. the value that appears in /node-palette and in
// the invoke URL. gateway-worker/deployment/deployment.yml sets WORKER_TYPE=gateway,
// overriding the "gateway-worker" default in its config.py — so BOTH names are live
// depending on the environment, and the deployed one is "gateway".
export const GATEWAY_WORKER_TYPE = "gateway";
export const GATEWAY_WORKER_TYPE_ALIASES = [GATEWAY_WORKER_TYPE, "gateway-worker"] as const;

/** The palette prefixes worker ids ("worker:gateway"); base_worker_id is the bare type. */
const bareWorkerType = (workerType: string): string =>
    workerType.toLowerCase().trim().replace(/^worker:/, "");

/** Is this base worker the gateway-worker?
 *
 *  Exact match against the known aliases — deliberately NOT a substring test. The
 *  old `.includes("gateway-worker")` silently returned false for the deployed type
 *  ("gateway"), which hid the whole Gateway Action tab and no test caught it,
 *  because every test only ever passed the literal string "gateway-worker". */
export function isGatewayWorker(workerType?: string): boolean {
    if (!workerType) return false;
    const bare = bareWorkerType(workerType);
    return (GATEWAY_WORKER_TYPE_ALIASES as readonly string[]).includes(bare);
}

// ── Field options sourced by INVOKING a gateway function ──────────────────────
// A select/combobox field can fill its options by invoking a gateway function via
// the control-plane worker-invoke proxy (POST). The function's response body is the
// options list. This rides the autoform serverOptions POST support.
// buildGatewayOptionsServerConfig / parseGatewayOptionsServerConfig / IGatewayOptionsConfig
// / GATEWAY_OPTIONS_* / IParamBinding now live in @ldc/autoform (see the re-export above).

export interface IGatewayBinding {
    inputFields: ISchemaFieldItem[];
    outputFields: ISchemaFieldItem[];
    /** Lifts the API result (gateway_result.body.*) into the declared outputs. */
    outputMapping: IKeyValuePair[];
    /** Merge into the node-definition's input_mapping. */
    marker: Record<string, unknown>;
}

// gateway ParamType → builder control + data type
function paramControl(type: string): { fieldControl: string; outputType: string } {
    switch (type) {
        case "number": return { fieldControl: "NumberControl", outputType: "number" };
        case "boolean": return { fieldControl: "SwitchControl", outputType: "boolean" };
        case "object": return { fieldControl: "TextareaControl", outputType: "object" };
        case "array": return { fieldControl: "TextareaControl", outputType: "array" };
        default: return { fieldControl: "InputControl", outputType: "string" };
    }
}

function field(key: string, label: string, type: string, required: boolean): ISchemaFieldItem {
    const { fieldControl, outputType } = paramControl(type);
    return { key, label: label || key, outputType, fieldControl, required, placeholder: "", description: "", options: "" };
}

// Resolver grammar is {{$source.path}} with NO spaces; the node's resolved input
// is exposed as `custom` in the _function_call resolution context.
const expr = (key: string) => `{{$custom.${key}}}`;

// Locked constants are typed by the param so body/number/bool args aren't sent as strings.
function coerceConstant(type: string, raw: string): unknown {
    switch (type) {
        case "number": return Number(raw);
        case "boolean": return raw === "true";
        case "object":
        case "array":
            try { return JSON.parse(raw); } catch { return raw; }
        default: return raw;
    }
}

const modeOf = (bindings: IParamBinding[], key: string): IParamBinding =>
    bindings.find((b) => b.key === key) ?? { key, mode: "expose" };

/** Exposed params become user-facing input fields. */
export function gatewayInputFields(params: IGatewayFunctionParam[], bindings: IParamBinding[]): ISchemaFieldItem[] {
    return params
        .filter((p) => modeOf(bindings, p.key).mode === "expose")
        .map((p) => field(p.key, p.label, p.type, !!p.required));
}

// Always surfaced alongside the body so downstream nodes can branch on the call:
// status_code (HTTP status) + error (set when the gateway rejected the call).
const META_OUTPUTS = [
    { key: "status_code", label: "Status code", type: "number" },
    { key: "error", label: "Error", type: "object" },
] as const;

// Meta outputs not already declared by the function (a same-named field wins).
const metaOutputs = (declaredKeys: Set<string>) => META_OUTPUTS.filter((m) => !declaredKeys.has(m.key));

/** The function's output_fields become the node's output schema (whole body as
 *  `response` when none declared), plus status_code/error for branching. */
export function gatewayOutputFields(fields: IGatewayOutputField[]): ISchemaFieldItem[] {
    const declared = fields.length
        ? fields.map((f) => field(f.key, f.label, f.type, false))
        : [field("response", "Response", "object", false)];
    const keys = new Set(declared.map((f) => f.key));
    return [...declared, ...metaOutputs(keys).map((m) => field(m.key, m.label, m.type, false))];
}

/** output_mapping lifting the upstream result into the declared outputs. Raw
 *  worker output is `{ gateway_result: { status_code, headers, body, error } }`;
 *  `body` is the API result, all addressed via `{{$_output...}}`. */
export function gatewayOutputMapping(fields: IGatewayOutputField[]): IKeyValuePair[] {
    const base = `{{$_output.${GATEWAY_RESULT_KEY}`;
    const declared = fields.length
        ? fields.map((f) => ({ key: f.key, value: `${base}.body.${f.key}}}` }))
        : [{ key: "response", value: `${base}.body}}` }];
    const keys = new Set(declared.map((p) => p.key));
    const meta = metaOutputs(keys).map((m) => ({ key: m.key, value: `${base}.${m.key}}}` }));
    return [...declared, ...meta];
}

/** The invoke_app_function marker keyed under `resultKey`.
 *
 *  The selector is the (app, function) NAME pair, not the function id: ids are
 *  minted per-environment, so an id-pinned definition breaks the moment it is
 *  promoted dev → qa → prod. The pair is stable, and a function name is unique
 *  within its app. The control plane reads `worker_function` + `params`. */
export function gatewayFunctionMarker(
    fn: IGatewayFunction,
    bindings: IParamBinding[],
    appName = "",
    resultKey = GATEWAY_RESULT_KEY,
): Record<string, unknown> {
    const args: Record<string, unknown> = {};
    for (const p of fn.input_params) {
        const b = modeOf(bindings, p.key);
        if (b.mode === "lock") {
            if (b.constant !== undefined && b.constant !== "") args[p.key] = coerceConstant(p.type, b.constant);
        } else {
            args[p.key] = expr(p.key);
        }
    }
    return {
        [resultKey]: {
            _function_call: {
                worker_function: GATEWAY_WORKER_FUNCTION,
                params: { app: appName, function: fn.name, args },
            },
        },
    };
}

export interface IParsedGatewayBinding {
    app: string;
    function: string;
    functionId: string;
    bindings: IParamBinding[];
}

/** Reverse a saved marker back into the section's selection so editing an
 *  existing gateway node reloads its app/function + per-param expose/lock.
 *
 *  Reads BOTH shapes: the name-addressed one written today, and the id-addressed
 *  one written before (which carried the names only in the `_binding` UI hint).
 *  Without the fallback, opening a node saved by an older build would silently
 *  clear its app/function pickers. */
export function parseGatewayMarker(marker?: Record<string, unknown> | null): IParsedGatewayBinding | null {
    if (!marker) return null;
    for (const v of Object.values(marker)) {
        const call = (v as { _function_call?: Record<string, unknown> })?._function_call;
        if (!call || call.worker_function !== GATEWAY_WORKER_FUNCTION) continue;
        const legacyHint = (call._binding ?? {}) as { app?: string; function?: string };
        const params = (call.params ?? {}) as {
            app?: string; function?: string; function_id?: string; args?: Record<string, unknown>;
        };
        const args = params.args ?? {};
        const bindings: IParamBinding[] = Object.entries(args).map(([key, val]) =>
            typeof val === "string" && val === expr(key)
                ? { key, mode: "expose" }
                : { key, mode: "lock", constant: typeof val === "string" ? val : JSON.stringify(val) },
        );
        return {
            app: String(params.app ?? legacyHint.app ?? ""),
            function: String(params.function ?? legacyHint.function ?? ""),
            functionId: String(params.function_id ?? ""),
            bindings,
        };
    }
    return null;
}

/**
 * Pull existing `_function_call` markers out of a saved input_mapping so they
 * survive a save. The builder's IMappingEntry round-trip only keeps known worker
 * fields and would otherwise drop these marker keys (gateway or hand-authored).
 */
export function extractFunctionCallMarkers(
    inputMapping?: Record<string, unknown> | null,
): Record<string, unknown> | null {
    if (!inputMapping) return null;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(inputMapping)) {
        if (v && typeof v === "object" && "_function_call" in (v as object)) out[k] = v;
    }
    return Object.keys(out).length ? out : null;
}

export function buildGatewayBinding(fn: IGatewayFunction, bindings: IParamBinding[], appName = ""): IGatewayBinding {
    return {
        inputFields: gatewayInputFields(fn.input_params, bindings),
        outputFields: gatewayOutputFields(fn.output_fields),
        outputMapping: gatewayOutputMapping(fn.output_fields),
        marker: gatewayFunctionMarker(fn, bindings, appName),
    };
}
