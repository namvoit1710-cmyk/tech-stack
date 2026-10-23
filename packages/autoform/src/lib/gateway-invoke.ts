/**
 * SA-2184 — the single source of truth for the Gateway invoke transport shared
 * by every Choice control's server-option hook (Select / RadioPopup /
 * Combobox; SelectTable in Phase B). No React here — pure constants + the
 * POST-body merge helper, so every hook imports the same thing instead of
 * re-implementing it.
 */

/** The control-plane service key that hosts `/api/v1/workers/...`. */
export const GATEWAY_INVOKE_SERVICE = "workflow";

/** The gateway-worker's registered type (deployment sets WORKER_TYPE=gateway). */
const GATEWAY_INVOKE_WORKER_TYPE = "gateway";

/** The high-level gateway-worker function that resolves internal/external apps. */
const GATEWAY_INVOKE_FUNCTION = "invoke_app_function";

/** `workers/gateway/functions/invoke_app_function/invoke` */
export const GATEWAY_INVOKE_ENDPOINT = `workers/${GATEWAY_INVOKE_WORKER_TYPE}/functions/${GATEWAY_INVOKE_FUNCTION}/invoke`;

/**
 * The invoke response double-wraps the upstream call: the control plane's
 * {status, data} envelope wraps the worker's own {status_code, headers, body}.
 * The real payload always lives at `data.result.body`.
 */
export const GATEWAY_INVOKE_DATAPATH = "data.result.body";

/** Combobox (typeahead) default: the typed term rides `args["$search"]`. */
export const DEFAULT_SEARCH_ARG_KEY = "$search";

/**
 * SA-1551 — merge serverOptions dependency values into a POST body.
 *
 * Gateway option sources send a fully-formed static `body` (e.g. `{ params: { function_id,
 * args } }`); credential-aware node functions instead want the picked dependency values
 * (credential_ref, team_id, …) forwarded as invoke args. We merge `depParams` under
 * `body.params.args` — the shape the control-plane worker-function invoke proxy reads —
 * without clobbering any static args already in the body. When there are no dependency
 * values, the body is returned unchanged (gateway path stays byte-identical).
 *
 * Exported for unit tests; pure (no React).
 */
export const buildPostBody = (
    body: Record<string, unknown> | undefined,
    depParams: Record<string, unknown> | undefined,
): Record<string, unknown> => {
    const base = body ? { ...body } : {};
    if (!depParams || Object.keys(depParams).length === 0) return base;

    const params = { ...(base.params as Record<string, unknown> | undefined ?? {}) };
    params.args = { ...(params.args ?? {}), ...depParams };
    base.params = params;
    return base;
};

export const GATEWAY_INVOKE_PAGED_DATAPATH = `${GATEWAY_INVOKE_DATAPATH}.data`;
export const GATEWAY_INVOKE_TOTALPATH = `${GATEWAY_INVOKE_DATAPATH}.total_count`;

/** Paged sources default to the OData names, which is what the file-service speaks. */
export const DEFAULT_PAGE_ARG_KEYS = { top: "$top", skip: "$skip", count: "$count" } as const;

export interface IPageArgKeys {
    top?: string;
    skip?: string;
    count?: string;
}

/**
 * Build the page args under the UPSTREAM's own parameter names.
 *
 * The gateway forwards only args declared in the function's `input_params`, and the
 * declared key IS the upstream query name — there is no rename step. So a paged source
 * whose upstream calls them `top`/`skip` never moves off page 1 while `$top`/`$skip` are
 * hard-coded, and it fails silently: same rows, no error. Binding the names per function
 * is what makes a gateway-backed paged source work against a non-OData upstream.
 *
 * An empty-string name means "do not send this arg" — an upstream with no count/total
 * parameter should not receive one. `countValue` differs by transport: the gateway
 * percent-encodes booleans as "True"/"False", which OData rejects, so the POST path
 * passes the string "true" while legacy GET keeps the real boolean.
 *
 * Pure; exported for unit tests.
 */
export const buildPageArgs = (
    page: { pageIndex: number; pageSize: number },
    keys: IPageArgKeys | undefined,
    countValue: unknown,
): Record<string, unknown> => {
    const top = keys?.top ?? DEFAULT_PAGE_ARG_KEYS.top;
    const skip = keys?.skip ?? DEFAULT_PAGE_ARG_KEYS.skip;
    const count = keys?.count ?? DEFAULT_PAGE_ARG_KEYS.count;

    const out: Record<string, unknown> = {};
    if (top) out[top] = page.pageSize;
    if (skip) out[skip] = page.pageIndex * page.pageSize;
    if (count) out[count] = countValue;
    return out;
};

/**
 * SA-1551 — merge serverOptions dependency values into a POST body.
 *
 * Gateway option sources send a fully-formed static `body` (e.g. `{ params: { function_id,
 * args } }`); credential-aware node functions instead want the picked dependency values
 * (credential_ref, team_id, …) forwarded as invoke args. We merge `depParams` under
 * `body.params.args` — the shape the control-plane worker-function invoke proxy reads —
 * without clobbering any static args already in the body. When there are no dependency
 * values, the body is returned unchanged (gateway path stays byte-identical).
 *
 * Exported for unit tests; pure (no React).
 */

/** Resolve a dot-notation path (`data.result.body`) against a response object. */
export const getByPath = (obj: unknown, path?: string): unknown => {
    if (!path) return obj;
    return path
        .split(".")
        .reduce<unknown>((acc, key) => (acc as Record<string, unknown> | undefined)?.[key], obj);
};

/** The subset of a `BuilderServices` entry the option transport actually calls. */
export interface IOptionsServiceHandler {
    fetch: (endpoint: string, params?: Record<string, unknown>) => Promise<unknown>;
    post: (endpoint: string, data?: unknown) => Promise<unknown>;
}

/** The `serverOptions` fields the transport reads. Kept structural so both the
 *  runtime hooks and the design-time authoring UI can pass their own config. */
export interface IOptionsRequestConfig {
    endpoint?: string;
    method?: "GET" | "POST";
    body?: Record<string, unknown>;
    responseMapping?: { dataPath?: string };
}

/**
 * Run one option-source request and return the rows at `responseMapping.dataPath`.
 *
 * This is the SINGLE place the request is actually issued (gotcha #7 — one
 * transport, one helper). `args` are the resolved dependency values: on the POST
 * (gateway) path they are merged under `body.params.args`; on the GET (legacy
 * REST) path they ride the query string. Callers supply `args` however they
 * resolve them — the runtime hooks watch form fields, the Form Builder's own
 * Gateway pickers pass the picked app straight in.
 */
export const fetchOptionRows = async (
    serviceEntry: IOptionsServiceHandler | undefined,
    config: IOptionsRequestConfig | undefined,
    args: Record<string, unknown> = {},
    fallbackDataPath = "data.items",
): Promise<Record<string, unknown>[]> => {
    if (!serviceEntry || !config?.endpoint) return [];
    const { endpoint, method = "GET", body, responseMapping } = config;

    const response = method === "POST"
        ? await serviceEntry.post(endpoint, buildPostBody(body, args))
        : await serviceEntry.fetch(endpoint, args);

    const rows = getByPath(response, responseMapping?.dataPath ?? fallbackDataPath);
    return Array.isArray(rows) ? (rows as Record<string, unknown>[]) : [];
};
