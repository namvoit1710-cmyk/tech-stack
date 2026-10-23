import { describe, expect, it, rs } from "@rstest/core";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@ldc/tanstack-query";
import type { ReactNode } from "react";

// A fake ISocket that records handlers so the test can drive events.
const handlers: Record<string, (arg: unknown) => void> = {};
const emit = rs.fn();
const disconnect = rs.fn();
const fakeSocket = {
    connected: true,
    on: (ev: string, cb: (arg: unknown) => void) => { handlers[ev] = cb; },
    off: rs.fn(),
    emit,
    disconnect: rs.fn(),
};
rs.mock("@/lib/socket", () => ({
    pushGatewaySocket: { connect: () => fakeSocket, disconnect },
}));
// use-worker-apps-query.ts is only imported here for its workerAppKeys constant,
// but its top-level `apiV2` import pulls in env validation (src/env.ts). Mock it,
// matching the convention every sibling API-hook test in this feature uses
// (worker-replicas.test.tsx, use-worker-apps-query.test.tsx, workflows.test.ts).
rs.mock("@/lib/api", () => ({ apiV2: { get: rs.fn() } }));

const { useWorkerHealthSync } = await import("./use-worker-health-sync");
const { workerAppKeys } = await import("./apis/use-worker-apps-query");
const { SocketEvent } = await import("@/features/workflows/types/socket-event");

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => {
    client = client ?? new QueryClient();
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};

describe("useWorkerHealthSync", () => {
    it("SocketEvent.WorkerAppHealth is the R14 event name the hook listens for", () => {
        expect(SocketEvent.WorkerAppHealth).toBe("worker.app.health");
    });

    it("emits wait{workers:health} on mount and reports connected", () => {
        client = new QueryClient();
        const { result } = renderHook(() => useWorkerHealthSync(), { wrapper });
        expect(emit).toHaveBeenCalledWith("wait", { key: "workers:health" });
        expect(result.current.connected).toBe(true);
    });

    it("a worker.app.health data_chunk patches the app-aggregate cache", () => {
        client = new QueryClient();
        client.setQueryData(workerAppKeys.all, [
            { worker_app: "jira-worker", delivery_mode: "push", nodes_total: 22, nodes_healthy: 22, replicas_total: 3, replicas_healthy: 3, replica_identity: "known", oldest_heartbeat_age_seconds: 3 },
        ]);
        renderHook(() => useWorkerHealthSync(), { wrapper });

        act(() => {
            handlers["data_chunk"]?.(JSON.stringify({
                data: {
                    event_type: "worker.app.health",
                    worker_app: "jira-worker",
                    delivery_mode: "push",
                    nodes_total: 22,
                    nodes_healthy: 20,
                    replicas_total: 3,
                    replicas_healthy: 1,
                    replica_identity: "known",
                    oldest_heartbeat_age_seconds: 120,
                    at: "x",
                },
            }));
        });

        const rows = client.getQueryData(workerAppKeys.all) as any[];
        expect(rows.find((r) => r.worker_app === "jira-worker").replicas_healthy).toBe(1);
    });

    it("ignores unrelated events", () => {
        client = new QueryClient();
        client.setQueryData(workerAppKeys.all, []);
        renderHook(() => useWorkerHealthSync(), { wrapper });
        act(() => {
            handlers["data_chunk"]?.(JSON.stringify({ data: { event_type: "node.completed" } }));
        });
        expect((client.getQueryData(workerAppKeys.all) as any[]).length).toBe(0);
    });

    it("ignores the retired R9 worker.replica.health event now that R14 rekeyed to worker.app.health", () => {
        // Discriminates a hook that still (or again) compares against
        // SocketEvent.WorkerReplicaHealth from one that was actually rekeyed:
        // this event_type would have matched the old comparison.
        client = new QueryClient();
        client.setQueryData(workerAppKeys.all, []);
        renderHook(() => useWorkerHealthSync(), { wrapper });
        act(() => {
            handlers["data_chunk"]?.(JSON.stringify({
                data: { event_type: "worker.replica.health", worker_type: "echo", delivery_mode: "pull", replicas_total: 3, replicas_healthy: 1, oldest_heartbeat_age_seconds: 120, at: "x" },
            }));
        });
        expect((client.getQueryData(workerAppKeys.all) as any[]).length).toBe(0);
    });

    it("emits leave and disconnects on unmount", () => {
        client = new QueryClient();
        emit.mockClear();
        const { unmount } = renderHook(() => useWorkerHealthSync(), { wrapper });
        unmount();
        expect(emit).toHaveBeenCalledWith("leave", { key: "workers:health" });
        expect(disconnect).toHaveBeenCalled();
    });
});
