import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook, waitFor } from "@testing-library/react";

import type { IEditorValue } from "@ldc/workflow-editor";
import type { IWorkflowDetailResponse, IWorkflowSavePayload } from "../types/workflows";

const useWorkflowPublishedMock = rs.fn();
const mapApiToWorkflowValueMock = rs.fn();
const mapWorkflowToSavePayloadMock = rs.fn();
const showMessageBoxMock = rs.fn();
const tMock = rs.fn((key: string) => key);
const fetchQueryMock = rs.fn();

rs.mock("./apis/workflows", () => ({
    useWorkflowPublished: useWorkflowPublishedMock,
    workflowKey: {
        getWorkflowByVersion: (workflowId: string, versionId: string) => ["workflows", workflowId, versionId],
    },
}));

rs.mock("../utils/node-mapper-utils", () => ({
    mapApiToWorkflowValue: mapApiToWorkflowValueMock,
}));

rs.mock("../utils/workflow-save-mapper", () => ({
    mapWorkflowToSavePayload: mapWorkflowToSavePayloadMock,
}));

rs.mock("@/components/providers/messagebox-provider", () => ({
    useMessageBox: () => showMessageBoxMock,
}));

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: tMock }),
}));

rs.mock("@ldc/tanstack-query", () => ({
    useQueryClient: () => ({ fetchQuery: fetchQueryMock }),
    // Every test leaves the URL's `?version` param unset, so `isPinned` is always false and
    // this query's own data/isLoading/isError are never asserted on — only its shape matters
    // (the hook reads `pinnedQuery.isError` unconditionally, so it must not be undefined).
    useQuery: rs.fn(() => ({ data: undefined, isLoading: false, isError: false })),
    useInfiniteQuery: rs.fn(),
    queryKeyFactory: rs.fn(() => ({
        all: [],
        lists: () => [],
        details: () => [],
    })),
}));

rs.mock("@/lib/api", () => ({
    __esModule: true,
    default: { get: rs.fn() },
}));

rs.mock("react-router-dom", () => ({
    useSearchParams: () => [new URLSearchParams(), rs.fn()],
}));

const mockVersionResponse = { data: { version: "ver-2", nodes: [], connections: [] } };

describe("useWorkflowDetail", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        fetchQueryMock.mockResolvedValue(mockVersionResponse);
        mapApiToWorkflowValueMock.mockReturnValue({ nodes: [], connections: [] });
        mapWorkflowToSavePayloadMock.mockReturnValue({});
    });

    afterEach(async () => {
        const { useEditorStore } = await import("../stores/editor-stores");
        act(() => useEditorStore.getState().resetEditorStore());
        await Promise.resolve(rs.restoreAllMocks());
    });

    it("seeds workflow data, baseline payload, and selected version from the detail response", async () => {
        const workflowResponse: IWorkflowDetailResponse = {
            ok: true,
            data: {
                id: "wf-1",
                name: "Workflow 1",
                version: "ver-2",
                status: "DRAFT",
                nodes: [],
                edges: [],
            },
        };
        const editorValue: IEditorValue = { nodes: [], connections: [] };
        const savePayload: IWorkflowSavePayload = {
            id: "wf-1",
            name: "Workflow 1",
            version: "ver-2",
            status: "DRAFT",
            nodes: [],
            edges: [],
        };

        useWorkflowPublishedMock.mockReturnValue({ data: workflowResponse, isLoading: false });
        mapApiToWorkflowValueMock.mockReturnValue(editorValue);
        mapWorkflowToSavePayloadMock.mockReturnValue(savePayload);

        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        const { result, unmount } = renderHook(() => useWorkflowDetail("wf-1"));

        await waitFor(() => {
            expect(result.current.workflow?.id).toBe("wf-1");
        });

        expect(useWorkflowPublishedMock).toHaveBeenCalledWith("wf-1", { enabled: true });
        expect(mapApiToWorkflowValueMock).toHaveBeenCalledWith(workflowResponse.data);
        expect(mapWorkflowToSavePayloadMock).toHaveBeenCalledWith(workflowResponse.data, editorValue, "ver-2");

        const state = useEditorStore.getState();
        expect(state.workflowData).toEqual(editorValue);
        expect(state.baselineSavePayload).toEqual(savePayload);
        expect(state.baseChangeVersion).toBe("ver-2");
        expect(state.selectedVersionId).toBe("ver-2");
        expect(state.isDirty).toBe(false);

        unmount();

        expect(useEditorStore.getState().workflowInfo).toBeNull();
        expect(useEditorStore.getState().baselineSavePayload).toBeNull();
        expect(useEditorStore.getState().baseChangeVersion).toBeNull();
        expect(useEditorStore.getState().selectedVersionId).toBeNull();
    });

    it("does not overwrite an explicitly switched version on later base-detail refetches", async () => {
        const workflowResponse: IWorkflowDetailResponse = {
            ok: true,
            data: {
                id: "wf-1",
                name: "Workflow 1",
                version: "current-version",
                status: "DRAFT",
                nodes: [],
                edges: [],
            },
        };
        const initialEditorValue: IEditorValue = { nodes: [], connections: [] };
        const switchedEditorValue: IEditorValue = {
            nodes: [{ id: "node-version-b" } as never],
            connections: [],
        };
        const initialSavePayload: IWorkflowSavePayload = {
            id: "wf-1",
            name: "Workflow 1",
            version: "current-version",
            status: "DRAFT",
            nodes: [],
            edges: [],
        };

        let currentQueryResult = { data: workflowResponse, isLoading: false };

        useWorkflowPublishedMock.mockImplementation(() => currentQueryResult);
        mapApiToWorkflowValueMock.mockReturnValue(initialEditorValue);
        mapWorkflowToSavePayloadMock.mockReturnValue(initialSavePayload);

        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        const { rerender } = renderHook(() => useWorkflowDetail("wf-1"));

        await waitFor(() => {
            expect(useEditorStore.getState().selectedVersionId).toBe("current-version");
        });

        act(() => {
            useEditorStore.getState().setWorkflowInfo({ ...workflowResponse.data, version: "version-record-id" });
            useEditorStore.getState().setSelectedVersionId("version-record-id");
            useEditorStore.getState().setBaselineSavePayload({
                id: "wf-1",
                name: "Version B",
                version: "version-record-id",
                status: "DRAFT",
                nodes: [{ id: "node-version-b" } as never],
                edges: [],
            });
            useEditorStore.getState().setWorkflowData(switchedEditorValue);
        });

        currentQueryResult = {
            data: { ...workflowResponse, data: { ...workflowResponse.data, updated_at: "2026-06-03T10:00:00.000Z" } },
            isLoading: false,
        };

        rerender();

        await waitFor(() => {
            expect(useEditorStore.getState().workflowData).toEqual(switchedEditorValue);
        });

        expect(useEditorStore.getState().selectedVersionId).toBe("version-record-id");
        expect(useEditorStore.getState().baselineSavePayload).toMatchObject({
            name: "Version B",
            nodes: [{ id: "node-version-b" }],
        });
    });
});

describe("useWorkflowDetail — switchVersion", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useWorkflowPublishedMock.mockReturnValue({ data: undefined, isLoading: false });
        fetchQueryMock.mockResolvedValue(mockVersionResponse);
        mapApiToWorkflowValueMock.mockReturnValue({ nodes: [], connections: [] });
        mapWorkflowToSavePayloadMock.mockReturnValue({});
    });

    afterEach(async () => {
        const { useEditorStore } = await import("../stores/editor-stores");
        act(() => useEditorStore.getState().resetEditorStore());
        await Promise.resolve(rs.restoreAllMocks());
    });

    it("switches when dirty without using version confirmation dialog", async () => {
        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        useEditorStore.setState({
            isDirty: true,
            selectedVersionId: "ver-1",
            unsavedDraft: { nodes: [{ id: "draft-node" }], connections: [] } as never,
            baseChangeVersion: "ver-1",
        });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        await act(async () => {
            await result.current.switchVersion("ver-2");
        });

        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(fetchQueryMock).toHaveBeenCalledTimes(1);
        expect(useEditorStore.getState().selectedVersionId).toBe("ver-2");
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-1");
        expect(useEditorStore.getState().isDirty).toBe(false);
    });

    it("switches immediately when not dirty", async () => {
        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        useEditorStore.setState({ isDirty: false, selectedVersionId: "ver-1" });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        await act(async () => {
            await result.current.switchVersion("ver-2");
        });

        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(fetchQueryMock).toHaveBeenCalledTimes(1);
        expect(useEditorStore.getState().selectedVersionId).toBe("ver-2");
    });

    it("keeps unsavedDraft after switching versions", async () => {
        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        const draft = { nodes: [{ id: "draft-node" }], connections: [] };
        useEditorStore.setState({
            isDirty: true,
            selectedVersionId: "ver-1",
            baseChangeVersion: "ver-1",
            unsavedDraft: draft as never,
        });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        await act(async () => {
            await result.current.switchVersion("ver-2");
        });

        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(fetchQueryMock).toHaveBeenCalledTimes(1);
        expect(useEditorStore.getState().selectedVersionId).toBe("ver-2");
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-1");
        expect(useEditorStore.getState().unsavedDraftBaseVersion).toBe("ver-1");
        expect(useEditorStore.getState().unsavedDraft).toEqual(draft);
    });

    it("does nothing when selecting the current version", async () => {
        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        useEditorStore.setState({ isDirty: true, selectedVersionId: "ver-1" });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        await act(async () => {
            await result.current.switchVersion("ver-1");
        });

        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(fetchQueryMock).not.toHaveBeenCalled();
    });

    it("restores unsaved draft when selecting the unsaved sentinel", async () => {
        const draft = { nodes: [{ id: "n1" }], connections: [] };

        const { useWorkflowDetail, UNSAVED_VERSION_SENTINEL } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        useEditorStore.setState({
            isDirty: false,
            selectedVersionId: "ver-2",
            baseChangeVersion: null,
            unsavedDraftBaseVersion: "ver-1",
            unsavedDraft: draft as never,
        });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        await act(async () => {
            await result.current.switchVersion(UNSAVED_VERSION_SENTINEL);
        });

        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(fetchQueryMock).not.toHaveBeenCalled();
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-1");
        expect(useEditorStore.getState().selectedVersionId).toBe(UNSAVED_VERSION_SENTINEL);
    });

    it("sets isSwitchingVersion during the fetch and clears it after", async () => {
        let resolveFetch!: (v: typeof mockVersionResponse) => void;
        fetchQueryMock.mockReturnValue(new Promise((res) => { resolveFetch = res; }));

        const { useWorkflowDetail } = await import("./use-workflow-detail-page");
        const { useEditorStore } = await import("../stores/editor-stores");
        useEditorStore.setState({ isDirty: false, selectedVersionId: "ver-1" });

        const { result } = renderHook(() => useWorkflowDetail("wf-1"));

        let switchPromise: Promise<void>;
        act(() => {
            switchPromise = result.current.switchVersion("ver-2");
        });

        await waitFor(() => expect(result.current.isLoading).toBe(true));

        await act(async () => {
            resolveFetch(mockVersionResponse);
            await switchPromise;
        });

        expect(result.current.isLoading).toBe(false);
    });
});
