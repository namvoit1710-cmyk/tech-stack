import { describe, expect, it, rs } from "@rstest/core";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@ldc/tanstack-query";
import type { ReactNode } from "react";

const getMock = rs.fn();
rs.mock("@/lib/api", () => ({ apiV2: { get: (...a: unknown[]) => getMock(...a) } }));

// Import AFTER the mock is registered.
const { useQueryWorkerApps, workerAppKeys } = await import("./use-worker-apps-query");
const { useQueryWorkerAppInstances } = await import("./use-worker-app-instances-query");

const wrapper = ({ children }: { children: ReactNode }) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};

describe("worker-apps query hooks", () => {
    it("key root is 'worker-apps', distinct from node-pallete.ts's 'workers' namespace", () => {
        expect(workerAppKeys.all[0]).toBe("worker-apps");
        expect(workerAppKeys.all[0]).not.toBe("workers");
        expect(workerAppKeys.instances("echo-app")).toContain("echo-app");
    });

    it("useQueryWorkerApps GETs /worker-apps", async () => {
        getMock.mockResolvedValueOnce([]);
        const { result } = renderHook(() => useQueryWorkerApps(), { wrapper });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(getMock).toHaveBeenCalledWith("/worker-apps");
    });

    it("useQueryWorkerAppInstances does NOT fetch while disabled", () => {
        getMock.mockClear();
        renderHook(() => useQueryWorkerAppInstances("echo-app", false), { wrapper });
        // Mounting a query fires its queryFn synchronously (inside the mount
        // effect, before renderHook returns) when the query is enabled. If
        // `enabled` were not wired through, this call would already have
        // happened by now — this is not "data is still undefined", it is
        // "the transport was never touched".
        expect(getMock).not.toHaveBeenCalled();
    });

    it("useQueryWorkerAppInstances GETs /worker-apps/{app}/instances when enabled", async () => {
        getMock.mockClear();
        getMock.mockResolvedValueOnce([]);
        const { result } = renderHook(() => useQueryWorkerAppInstances("echo-app", true), { wrapper });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(getMock).toHaveBeenCalledWith("/worker-apps/echo-app/instances");
    });

    it("encodes special characters in the worker_app path segment", async () => {
        getMock.mockClear();
        getMock.mockResolvedValueOnce([]);
        const { result } = renderHook(() => useQueryWorkerAppInstances("echo app/v2", true), { wrapper });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(getMock).toHaveBeenCalledWith("/worker-apps/echo%20app%2Fv2/instances");
    });
});
