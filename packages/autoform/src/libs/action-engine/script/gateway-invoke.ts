import type { BuilderServices } from "../../../hooks/use-builder-services";
import type { GatewayInvoke } from "./action-script-ctx";

export const GATEWAY_SERVICE = "workflow";
export const GATEWAY_ENDPOINT = "workers/gateway/functions/invoke_app_function/invoke";

interface GatewayResult { status_code?: number; headers?: Record<string, unknown>; body?: unknown; error?: unknown }
interface GatewayEnvelope { status?: unknown; result?: GatewayResult }
// The @ldc/api-sdk response interceptor returns `response.data` — the control-plane REST
// envelope `{ status, data }`. The worker-invoke payload (`{ status, result }`) sits under
// `.data`; a pre-unwrapped caller returns that inner envelope directly, so accept both.
interface GatewayOuterEnvelope extends GatewayEnvelope { data?: GatewayEnvelope }
export interface GatewayInvokeError extends Error { status_code?: number; body?: unknown; error?: unknown }

export function makeGatewayInvoke(services?: BuilderServices): GatewayInvoke {
    return {
        async invoke<T = unknown>(app: string, fn: string, args: Record<string, unknown> = {}): Promise<T> {
            const service = services?.[GATEWAY_SERVICE];
            if (!service || typeof service.post !== "function") {
                throw new Error(`[gateway.invoke] service "${GATEWAY_SERVICE}" is not configured`);
            }
            // ServiceHandler.post resolves to the parsed JSON body (APISdk interceptor).
            const raw = (await service.post(GATEWAY_ENDPOINT, { params: { app, function: fn, args } })) as GatewayOuterEnvelope;
            // Unwrap the control-plane `{ status, data }` envelope; fall back to `raw` if a
            // caller already unwrapped it.
            const envelope: GatewayEnvelope = raw?.data?.result ? raw.data : raw;
            const result = envelope?.result;
            if (!result) throw new Error("[gateway.invoke] malformed response: missing `result`");
            if (typeof result.status_code === "number" && result.status_code >= 400) {
                const err = new Error(`[gateway.invoke] ${app}.${fn} failed (${result.status_code})`) as GatewayInvokeError;
                err.status_code = result.status_code;
                err.body = result.body;
                err.error = result.error;
                throw err;
            }
            return result.body as T;
        },
    };
}
