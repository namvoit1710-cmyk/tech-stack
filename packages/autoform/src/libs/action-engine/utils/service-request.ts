import { ServiceHandler } from "../../../hooks/use-builder-services";

export async function serviceRequest(
    service: ServiceHandler,
    method: string,
    url: string,
    body?: unknown,
    headers?: Record<string, string>
): Promise<unknown> {
    if (method === "GET") {
        return service.fetch(url, body as Record<string, any>, headers);
    }
    if (method === "DELETE") {
        return service.delete(url, headers);
    }
    const methodFn = service[method.toLowerCase() as keyof ServiceHandler];
    console.log(`[serviceRequest] Calling service method ${method} at ${url} with body:`, body, "and headers:", headers);
    if (typeof methodFn === "function") {
        return (methodFn as any)(url, body as Record<string, any>, headers);
    }

    throw new Error(
        `[serviceRequest] Service does not support method: ${method}`
    );
}