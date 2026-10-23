// Types for the Gateway Admin feature. Field names mirror the control-plane
// projections (workflow-control-plane-service gateway_* use cases) exactly.

export type GatewayAppKind = "internal" | "external";
export type GatewayAuthType = "none" | "bearer" | "api_key_header" | "oauth_cc";
export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS";
export type ParamType = "string" | "number" | "boolean" | "object" | "array";
export type ParamLocation = "path" | "query" | "header" | "body";

export const PARAM_TYPES: ParamType[] = ["string", "number", "boolean", "object", "array"];
export const PARAM_LOCATIONS: ParamLocation[] = ["path", "query", "header", "body"];
export const HTTP_METHODS: HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
// oauth_cc is accepted by the domain but not implemented at runtime — omit from the UI.
export const AUTH_TYPES: GatewayAuthType[] = ["none", "bearer", "api_key_header"];

export interface IGatewayAuthSummary {
    credential_present: boolean;
    key_version: number;
    last_rotated_at: string;
}

export interface IGatewayApp {
    id: string;
    name: string;
    kind: GatewayAppKind;
    base_url: string;
    allowed_hosts: string[];
    auth_type: GatewayAuthType;
    auth_summary: IGatewayAuthSummary;
    is_active: boolean;
    description: string;
    version: number;
    created_by: string;
    created_at: string;
    updated_by: string;
    updated_at: string;
}

export interface IGatewayFunctionParam {
    key: string;
    type: ParamType;
    location: ParamLocation;
    required: boolean;
    label: string;
}

export interface IGatewayOutputField {
    key: string;
    type: ParamType;
    label: string;
}

export interface IGatewayFunction {
    id: string;
    app_id: string;
    name: string;
    description: string;
    method: HttpMethod;
    path_template: string;
    input_params: IGatewayFunctionParam[];
    output_fields: IGatewayOutputField[];
    timeout_ms: number;
    icon: string;
    color: string;
    tags: string[];
    is_active: boolean;
    version: number;
    created_by: string;
    created_at: string;
    updated_by: string;
    updated_at: string;
}

// --- request payloads ---

export interface ICreateGatewayAppPayload {
    name: string;
    kind: GatewayAppKind;
    base_url?: string;
    allowed_hosts?: string[];
    auth_type: GatewayAuthType;
    auth_config?: Record<string, unknown>;
    description?: string;
    spec_url?: string;
}

// PATCH semantics: omit a field to leave it unchanged. spec_url "" clears it.
export interface IUpdateGatewayAppPayload {
    name?: string;
    base_url?: string;
    allowed_hosts?: string[];
    auth_type?: GatewayAuthType;
    auth_config?: Record<string, unknown>;
    description?: string;
    spec_url?: string;
}

export interface ICreateGatewayFunctionPayload {
    name: string;
    method: HttpMethod;
    path_template: string;
    input_params?: IGatewayFunctionParam[];
    output_fields?: IGatewayOutputField[];
    description?: string;
    timeout_ms?: number;
    icon?: string;
    color?: string;
    tags?: string[];
}

export type IUpdateGatewayFunctionPayload = Partial<ICreateGatewayFunctionPayload>;

// --- responses ({status, data}-wrapped by the response middleware) ---

export interface IEnvelope<T> {
    status: string;
    data: T;
}

// Error envelope returned by the control plane (response_envelope.error_response).
export interface IGatewayErrorBody {
    status?: string;
    error?: { code?: string; message?: string };
}

export type IGatewayAppListResponse = IEnvelope<{ items: IGatewayApp[]; total: number }>;
export type IGatewayAppDetailResponse = IEnvelope<{ app: IGatewayApp }>;
export type IGatewayMutateResponse = IEnvelope<{ id: string; version: number }>;
export type IGatewayDeleteResponse = IEnvelope<Record<string, unknown>>;
export type IGatewayTestResponse = IEnvelope<{
    reachable: boolean;
    status_code: number;
    latency_ms: number;
    upstream_error: string;
}>;
export type IGatewaySyncResponse = IEnvelope<{
    app: string;
    created: number;
    updated: number;
    deleted: number;
    unchanged: number;
    failed: number;
    skipped: boolean;
}>;
export type IGatewayFunctionListResponse = IEnvelope<{ items: IGatewayFunction[]; total: number }>;
export type IGatewayFunctionDetailResponse = IEnvelope<{ function: IGatewayFunction }>;
