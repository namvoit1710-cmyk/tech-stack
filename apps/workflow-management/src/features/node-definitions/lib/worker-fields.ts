/**
 * Metadata describing how each worker input field should be edited in the
 * input_mapping UI, keyed by worker type so mappings degrade gracefully for
 * non-HTTP workers.
 *
 * Derived from node-definition-mapping-guide.md — HTTP Request Worker Reference.
 */

export type WorkerFieldMode =
    | "expression"       // simple value or expression string
    | "key_value_array"  // array of { nameKey: "...", valueKey: "..." } objects
    | "json_template";   // textarea where the user composes JSON mixing custom fields

export interface IWorkerFieldMeta {
    key: string;
    label: string;
    description: string;
    mode: WorkerFieldMode;
    /** For key_value_array: column names */
    nameKey?: string;
    valueKey?: string;
    nameLabel?: string;
    valueLabel?: string;
}

/** The default worker type used when a worker has no registered field metadata. */
export const DEFAULT_WORKER_TYPE = "http-request-worker";

/** The HTTP worker's bare worker_type — the value `base_worker_id` / the palette
 *  `worker_id` actually carry (see the http worker's `WORKER_TYPE = "http_request"`).
 *  Kept distinct from {@link DEFAULT_WORKER_TYPE} (the service-name form) so the
 *  curated registry matches HTTP nodes regardless of which id they were saved with. */
export const HTTP_WORKER_TYPE = "http_request";

/** All fields the http-request-worker accepts, with the editor mode that best
 *  fits the data shape the backend expects. */
export const HTTP_WORKER_FIELDS: IWorkerFieldMeta[] = [
    { key: "method", label: "HTTP Method", description: "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS", mode: "expression" },
    { key: "url", label: "URL", description: "Full URL (supports expressions for path params)", mode: "expression" },
    {
        key: "query_params", label: "Query Parameters", description: "URL query string parameters",
        mode: "key_value_array", nameKey: "param_name", valueKey: "param_value", nameLabel: "Parameter", valueLabel: "Value",
    },
    {
        key: "headers", label: "Headers", description: "HTTP request headers",
        mode: "key_value_array", nameKey: "header_name", valueKey: "header_value", nameLabel: "Header", valueLabel: "Value",
    },
    { key: "body_type", label: "Body Type", description: "none, raw_json, raw_text, structured, form_urlencoded", mode: "expression" },
    { key: "body", label: "Request Body", description: "JSON body — compose from user input fields", mode: "json_template" },
    {
        key: "body_fields", label: "Body Fields (structured)", description: "Structured body fields (form/urlencoded)",
        mode: "key_value_array", nameKey: "field_name", valueKey: "field_value", nameLabel: "Field", valueLabel: "Value",
    },
    // The ceiling is the http worker's HTTP_MAX_TIMEOUT_SECONDS (deployment config,
    // 300 by default) and is enforced by the rule on its published input_schema.
    // Do not restate the number here — this hint does not track the deployment.
    { key: "timeout_seconds", label: "Timeout (seconds)", description: "Request timeout in seconds", mode: "expression" },
    { key: "auth_type", label: "Auth Type", description: "none, bearer, basic, api_key", mode: "expression" },
    { key: "auth_token", label: "Auth Token", description: "Token for bearer or api_key auth", mode: "expression" },
    { key: "auth_username", label: "Auth Username", description: "Username for basic auth", mode: "expression" },
    { key: "auth_password", label: "Auth Password", description: "Password for basic auth", mode: "expression" },
    { key: "api_key_header", label: "API Key Header Name", description: "Header name for api_key auth (default: X-API-Key)", mode: "expression" },
    { key: "follow_redirects", label: "Follow Redirects", description: "Default: true", mode: "expression" },
    { key: "verify_ssl", label: "Verify SSL", description: "Default: true", mode: "expression" },
];

/** A field the worker *produces* — used to suggest `{{$_output.<key>}}` expressions. */
export interface IWorkerOutputField {
    key: string;
    type: string;
    description: string;
}

/** Output fields the http-request-worker returns. */
export const HTTP_WORKER_OUTPUT_FIELDS: IWorkerOutputField[] = [
    { key: "status_code", type: "number", description: "HTTP status code" },
    { key: "response_body", type: "string | object", description: "Parsed response body" },
    { key: "response_headers", type: "object", description: "Response headers" },
    { key: "content_type", type: "string", description: "Content-Type header" },
    { key: "elapsed_ms", type: "number", description: "Request duration (ms)" },
    { key: "is_success", type: "boolean", description: "true if 2xx status" },
    { key: "final_url", type: "string", description: "Final URL after redirects" },
];

/** Registry of hand-curated worker field metadata keyed by worker type. These
 *  carry rich editor modes (key_value_array / json_template) that can't be
 *  inferred from a flat schema, so a curated entry always wins over derivation.
 *  Registered under both the service-name and bare-worker_type forms. */
const WORKER_FIELDS: Record<string, IWorkerFieldMeta[]> = {
    [DEFAULT_WORKER_TYPE]: HTTP_WORKER_FIELDS,
    [HTTP_WORKER_TYPE]: HTTP_WORKER_FIELDS,
};

const WORKER_OUTPUT_FIELDS: Record<string, IWorkerOutputField[]> = {
    [DEFAULT_WORKER_TYPE]: HTTP_WORKER_OUTPUT_FIELDS,
    [HTTP_WORKER_TYPE]: HTTP_WORKER_OUTPUT_FIELDS,
};

/** Output fields for a worker type (falls back to HTTP for unknown workers). */
export function getWorkerOutputFields(workerType?: string): IWorkerOutputField[] {
    if (workerType && WORKER_OUTPUT_FIELDS[workerType]) return WORKER_OUTPUT_FIELDS[workerType];
    return HTTP_WORKER_OUTPUT_FIELDS;
}

/** All known fields for a worker type (falls back to HTTP for unknown workers). */
export function getWorkerFields(workerType?: string): IWorkerFieldMeta[] {
    if (workerType && WORKER_FIELDS[workerType]) return WORKER_FIELDS[workerType];
    return HTTP_WORKER_FIELDS;
}

/** Metadata for a single field of a worker type. */
export function getWorkerFieldMeta(key: string, workerType?: string): IWorkerFieldMeta | undefined {
    return getWorkerFields(workerType).find((f) => f.key === key);
}

/* ── Schema-driven worker fields ─────────────────────────────────────────── */

/** A field from the base worker's catalog `input_schema` (the camelCase AutoForm
 *  shape served by the node palette). */
export interface IWorkerInputSchemaField {
    key: string;
    outputType?: string;
    fieldConfig?: {
        wrapperProps?: { label?: string };
        controlProps?: { placeholder?: string };
        label?: string;
        [k: string]: any;
    };
}

/** Editor mode inferred from a field's data type when the worker has no curated
 *  metadata: structured types get a JSON composer, scalars a value/expression input. */
function modeForOutputType(outputType?: string): WorkerFieldMode {
    return outputType === "object" || outputType === "array" ? "json_template" : "expression";
}

/** Derive input-mapping field metadata from the base worker's declared
 *  `input_schema`, so the mapping UI reflects the ACTUAL selected worker rather
 *  than the HTTP default. Returns `[]` when no schema is available. */
export function workerFieldsFromSchema(
    schema: IWorkerInputSchemaField[] | undefined | null,
): IWorkerFieldMeta[] {
    if (!schema?.length) return [];
    return schema
        .filter((f) => f.key)
        .map((f) => ({
            key: f.key,
            label: f.fieldConfig?.wrapperProps?.label || f.fieldConfig?.label || f.key,
            description: f.fieldConfig?.controlProps?.placeholder || "",
            mode: modeForOutputType(f.outputType),
        }));
}

/** Effective input-mapping fields for a worker. A hand-curated registry entry
 *  (rich modes, e.g. HTTP) wins; otherwise the fields are derived from the base
 *  worker's own `input_schema`; finally we fall back to the HTTP default for an
 *  unknown worker with no schema available. */
export function resolveWorkerFields(
    workerType?: string,
    schema?: IWorkerInputSchemaField[] | null,
): IWorkerFieldMeta[] {
    if (workerType && WORKER_FIELDS[workerType]) return WORKER_FIELDS[workerType];
    const derived = workerFieldsFromSchema(schema);
    if (derived.length) return derived;
    return HTTP_WORKER_FIELDS;
}
