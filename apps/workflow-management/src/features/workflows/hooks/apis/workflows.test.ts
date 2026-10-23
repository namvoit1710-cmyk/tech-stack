import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import type {
    IPublishWorkflowPayload,
    IPublishWorkflowResponse,
    IWorkflowListResponse,
    IWorkflowVersionListResponse,
} from "../../types/workflows";
import { formatVersionLabel } from "@/features/workflows/utils/version-utils";

const apiGet = rs.fn();
const apiPost = rs.fn();
const workflowAgentApiPost = rs.fn();

interface QueryConfig {
    queryKey: unknown;
    queryFn: () => Promise<unknown>;
    enabled?: boolean;
}

interface MutationConfig {
    mutationFn: (variables: unknown) => Promise<unknown>;
}

let latestQueryConfig: QueryConfig | undefined;
let latestMutationConfig: MutationConfig | undefined;

const useInfiniteQueryMock = rs.fn((config: unknown) => config);
const useMutationMock = rs.fn((config: MutationConfig) => {
    latestMutationConfig = config;
    return {};
});
const useQueryMock = rs.fn((config: QueryConfig) => {
    latestQueryConfig = config;
    return {};
});

rs.mock("@/lib/api", () => ({
    __esModule: true,
    default: {
        get: apiGet,
        post: apiPost,
    },
    workflowAgentApi: {
        post: workflowAgentApiPost,
    },
}));

rs.mock("@ldc/tanstack-query", () => ({
    __esModule: true,
    queryKeyFactory: (scope: string) => ({
        all: [scope],
        lists: () => [scope, "list"],
        details: () => [scope, "detail"],
    }),
    useInfiniteQuery: useInfiniteQueryMock,
    useMutation: useMutationMock,
    useQuery: useQueryMock,
}));

describe("workflow version types", () => {
    void beforeEach(() => {
        void Promise.resolve(rs.resetModules());
        void Promise.resolve(rs.clearAllMocks());
        latestQueryConfig = undefined;
        latestMutationConfig = undefined;
    });

    it("supports a version summary fixture", () => {
        const versionSummaryFixture: IWorkflowVersionListResponse = {
            ok: true,
            data: {
                currentVersion: "v3",
                publishVersion: "v2",
                versions: [
                    {
                        id: "ver-3",
                        version: "v3",
                        status: "draft",
                        isCurrent: true,
                        isPublished: false,
                    },
                ],
            },
        };

        expect(versionSummaryFixture.data.versions[0]?.version).toBe("v3");
        expect(versionSummaryFixture.data.publishVersion).toBe("v2");
    });

    it("supports publish payload and response types", () => {
        const payload: IPublishWorkflowPayload = {
            version: "ver-3",
            is_confirm: true,
        };
        const response: IPublishWorkflowResponse = {
            ok: true,
        };

        expect(payload.version).toBe("ver-3");
        expect(response.ok).toBe(true);
    });

    it("requests workflow versions", async () => {
        const response: IWorkflowVersionListResponse = {
            ok: true,
            data: {
                currentVersion: "v1",
                publishVersion: null,
                versions: [],
            },
        };
        apiGet.mockResolvedValue(response);

        const { useWorkflowVersionList, workflowKey } = await import("./workflows");
        useWorkflowVersionList("wf-1");
        expect(latestQueryConfig).toBeDefined();
        const result = await latestQueryConfig?.queryFn();

        expect(useQueryMock).toHaveBeenCalledWith({
            queryKey: workflowKey.getWorkflowVersions("wf-1"),
            queryFn: expect.any(Function),
            enabled: true,
        });
        expect(result).toBe(response);
        expect(apiGet).toHaveBeenCalledWith("/workflows/wf-1/versions");
    });

    it("requests workflow detail by version", async () => {
        const response = {
            ok: true,
            data: {
                id: "wf-1",
                name: "WF",
                status: "DRAFT",
                nodes: [],
                edges: [],
            },
        };
        apiGet.mockResolvedValue(response);

        const { useWorkflowByVersion, workflowKey } = await import("./workflows");
        useWorkflowByVersion("wf-1", "ver-1");
        expect(latestQueryConfig).toBeDefined();
        const result = await latestQueryConfig?.queryFn();

        expect(useQueryMock).toHaveBeenCalledWith({
            queryKey: workflowKey.getWorkflowByVersion("wf-1", "ver-1"),
            queryFn: expect.any(Function),
            enabled: true,
        });
        expect(result).toBe(response);
        expect(apiGet).toHaveBeenCalledWith("/workflows/wf-1/versions/ver-1");
    });

    it("publishes the workflow version", async () => {
        const response: IPublishWorkflowResponse = {
            ok: true,
        };
        apiPost.mockResolvedValue(response);

        const { usePublishWorkflow } = await import("./workflows");
        usePublishWorkflow();
        expect(latestMutationConfig).toBeDefined();
        const result = await latestMutationConfig?.mutationFn({
            workflowId: "wf-1",
            payload: { version: "ver-1", is_confirm: true },
        });

        expect(useMutationMock).toHaveBeenCalledWith({
            mutationFn: expect.any(Function),
        });
        expect(result).toBe(response);
        expect(apiPost).toHaveBeenCalledWith("/workflows/publish/wf-1", {
            version: "ver-1",
            is_confirm: true,
        });
    });

    it("fetches workflow versions with infinite scroll", async () => {
        const response: IWorkflowListResponse = {
            status: "ok",
            data: {
                items: [
                    {
                        id: "wf-1",
                        name: "Workflow 1",
                        version: "v1",
                        status: "DRAFT",
                        nodes: [],
                        edges: [],
                    },
                ],
                total: 1,
                $skip: 0,
                $top: 10,
            },
        };
        apiGet.mockResolvedValue(response);

        const { useWorkflowVersionListInfinite, workflowKey } = await import("./workflows");
        useWorkflowVersionListInfinite("wf-1", {});

        expect(useInfiniteQueryMock).toHaveBeenCalledWith(
            expect.objectContaining({
                queryKey: workflowKey.getWorkflowVersionsInfinite("wf-1", ""),
                initialPageParam: 0,
            })
        );
    });

    it("passes search filter to the version list endpoint", async () => {
        const { useWorkflowVersionListInfinite } = await import("./workflows");
        useWorkflowVersionListInfinite("wf-1", { search: "v2" });

        expect(useInfiniteQueryMock).toHaveBeenCalledWith(
            expect.objectContaining({
                queryKey: expect.arrayContaining([{ search: "v2" }]),
            })
        );
    });

    it("useExecutionLogDetail builds correct query key and calls API", async () => {
        const response = {
            status: "ok",
            data: {
                summary: {
                    run_id: "run-1",
                    workflow_id: "wf-1",
                    status: "COMPLETED",
                    started_at: "2026-01-01T00:00:00Z",
                    completed_at: "2026-01-01T00:01:00Z",
                    run_ids: ["run-1"],
                    total_runs: 1,
                    total_events: 3,
                    node_counts: { completed: 1, failed: 0, skipped: 0 },
                },
                events: [],
            },
        };
        apiGet.mockResolvedValue(response);

        const { useExecutionLogDetail, workflowKey } = await import("./workflows");
        useExecutionLogDetail("run-1");
        expect(latestQueryConfig).toBeDefined();
        const result = await latestQueryConfig?.queryFn();

        expect(useQueryMock).toHaveBeenCalledWith({
            queryKey: workflowKey.executionLog("run-1"),
            queryFn: expect.any(Function),
            enabled: true,
        });
        expect(result).toBe(response);
        expect(apiGet).toHaveBeenCalledWith("/runs/run-1/execution-log", {
            params: { offset: 0, include_children: true },
        });
    });

    it("useExecutionLogDetail enabled is false when runId is empty", async () => {
        const { useExecutionLogDetail } = await import("./workflows");
        useExecutionLogDetail("");

        expect(useQueryMock).toHaveBeenCalledWith(
            expect.objectContaining({ enabled: false })
        );
    });

    it("executionLog query key is a child of workflowKeys.all", async () => {
        const { workflowKey } = await import("./workflows");

        const logKey = workflowKey.executionLog("run-x");
        const allKey = workflowKey.all;

        expect(logKey[0]).toBe(allKey[0]);
        expect(logKey.slice(0, allKey.length)).toEqual(allKey);
    });

    it("useExecutionLogDetail queryFn returns API data on success", async () => {
        const response = {
            status: "ok",
            data: {
                summary: {
                    run_id: "run-2",
                    workflow_id: "wf-2",
                    status: "FAILED",
                    started_at: "2026-01-01T00:00:00Z",
                    completed_at: "2026-01-01T00:00:30Z",
                    run_ids: ["run-2"],
                    total_runs: 1,
                    total_events: 5,
                    node_counts: { completed: 2, failed: 1, skipped: 1 },
                },
                events: [{ index: 0, event_id: "evt-1", event_type: "RunStatusChanged", topic: "run.status", timestamp: "2026-01-01T00:00:00Z", run_id: "run-2", depth: 0, sequence: 1, causation_id: "", payload: {} }],
            },
        };
        apiGet.mockResolvedValue(response);

        const { useExecutionLogDetail } = await import("./workflows");
        useExecutionLogDetail("run-2");
        const result = await latestQueryConfig?.queryFn();

        expect(result).toEqual(response);
        expect(result?.data.summary.status).toBe("FAILED");
        expect(result?.data.events).toHaveLength(1);
    });
});

describe("formatVersionLabel", () => {
    it("returns first 8 chars of a UUID version string", () => {
        expect(formatVersionLabel("f292f4ab-c79e-4a1d-abe2-163f302793a9")).toBe("f292f4ab");
    });

    it("returns first 8 chars of a short string", () => {
        expect(formatVersionLabel("abcdefghijklmn")).toBe("abcdefgh");
    });

    it("returns the full string if shorter than 8 chars", () => {
        expect(formatVersionLabel("abc")).toBe("abc");
    });

    it("returns '—' for null", () => {
        expect(formatVersionLabel(null)).toBe("—");
    });

    it("returns '—' for undefined", () => {
        expect(formatVersionLabel(undefined)).toBe("—");
    });

    it("returns '—' for empty string", () => {
        expect(formatVersionLabel("")).toBe("—");
    });
});