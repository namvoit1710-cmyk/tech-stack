import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import type { IEditorValue } from "@ldc/workflow-editor";
import { useEditorStore } from "../stores/editor-stores";

const showMessageBoxMock = rs.fn();
const invalidateQueriesMock = rs.fn();
const mutateAsyncMock = rs.fn();
const toastSuccessMock = rs.fn();
const toastErrorMock = rs.fn();
const tMock = rs.fn((key: string) => key);
const mapWorkflowToSavePayloadMock = rs.fn();

rs.mock("@/components/providers/messagebox-provider", () => ({
    useMessageBox: () => showMessageBoxMock,
}));

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: tMock }),
}));

rs.mock("@/hooks/use-last-ref", () => ({
    useLatestRef: <T,>(value: T) => ({ current: value }),
}));

rs.mock("@ldc/tanstack-query", () => ({
    useQueryClient: () => ({
        invalidateQueries: invalidateQueriesMock,
    }),
}));

rs.mock("@ldc/ui/blocks/toast/toast", () => ({
    toast: {
        success: toastSuccessMock,
        error: toastErrorMock,
    },
}));

rs.mock("../utils/workflow-save-mapper", () => ({
    mapWorkflowToSavePayload: mapWorkflowToSavePayloadMock,
}));

rs.mock("./apis/workflows", () => ({
    workflowKey: {
        all: ["workflows"],
    },
    useUpdateWorkflow: (options?: { onSuccess?: (response: { data: { version?: string } }) => void }) => ({
        mutateAsync: async (variables: unknown) => {
            const result = await mutateAsyncMock(variables);
            options?.onSuccess?.(result);
            return result;
        },
        isPending: false,
    }),
}));

rs.mock("react-router-dom", () => ({
    useSearchParams: () => [new URLSearchParams(), rs.fn()],
}));

describe("useSaveWorkflow", () => {
    let dateNowSpy: ReturnType<typeof rs.spyOn>;

    beforeEach(() => {
        void Promise.resolve(rs.resetModules());
        void Promise.resolve(rs.clearAllMocks());
        useEditorStore.getState().resetEditorStore();
        dateNowSpy = rs.spyOn(Date, "now").mockReturnValue(1000);

        useEditorStore.setState({
            workflowInfo: {
                id: "wf-1",
                name: "Workflow 1",
                status: "DRAFT",
                version: "ver-1",
                nodes: [],
                edges: [],
            },
            workflowData: { nodes: [], connections: [] },
            baseChangeVersion: "ver-1",
            isDirty: true,
        });

        mapWorkflowToSavePayloadMock.mockImplementation((workflow) => ({
            id: workflow.id,
            name: workflow.name,
            status: workflow.status,
            nodes: [{ id: `saved-${workflow.name}` }],
            edges: [],
        }));
    });

    it("retries save with confirmation flag after 409 conflict and resets local baseline", async () => {
        mutateAsyncMock
            .mockRejectedValueOnce({ response: { status: 409 } })
            .mockResolvedValueOnce({
                ok: true,
                data: {
                    id: "wf-1",
                    name: "Workflow 1",
                    status: "DRAFT",
                    version: "ver-2",
                    nodes: [],
                    edges: [],
                },
            });
        showMessageBoxMock.mockResolvedValue(true);

        const { default: useSaveWorkflow } = await import("./use-save-workflow");
        const { result } = renderHook(() => useSaveWorkflow());

        await act(async () => {
            await result.current.onSave("wf-1");
        });

        expect(mutateAsyncMock).toHaveBeenCalledTimes(2);
        expect(mutateAsyncMock.mock.calls[0]?.[0]).toEqual({
            workflowId: "wf-1",
            payload: {
                id: "wf-1",
                name: "Workflow 1",
                status: "DRAFT",
                nodes: [{ id: "saved-Workflow 1" }],
                edges: [],
            },
        });
        expect(mutateAsyncMock.mock.calls[1]?.[0]).toEqual({
            workflowId: "wf-1",
            payload: {
                id: "wf-1",
                name: "Workflow 1",
                status: "DRAFT",
                nodes: [{ id: "saved-Workflow 1" }],
                edges: [],
                is_confirm: true,
            },
        });
        expect(showMessageBoxMock).toHaveBeenCalledWith(
            "workflow_save_conflict_message",
            "workflow_save_conflict_title",
        );
        expect(mapWorkflowToSavePayloadMock).toHaveBeenCalledTimes(1);
        expect(mapWorkflowToSavePayloadMock).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "wf-1",
                name: "Workflow 1",
                status: "DRAFT",
                version: "ver-1",
            }),
            { nodes: [], connections: [] },
            "ver-1",
        );
        expect(useEditorStore.getState().selectedVersionId).toBe("ver-2");
        expect(useEditorStore.getState().isDirty).toBe(false);
        expect(useEditorStore.getState().baselineSavePayload).toMatchObject({
            id: "wf-1",
            name: "Workflow 1",
            nodes: [{ id: "saved-Workflow 1" }],
        });
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-2");
        expect(invalidateQueriesMock).toHaveBeenCalledWith({ queryKey: ["workflows"] });
        expect(toastSuccessMock).toHaveBeenCalledWith(
            "notification.success",
            "notification.workflow_saved_successfully",
        );
        expect(toastErrorMock).not.toHaveBeenCalled();
    });

    it("serializes the unsaved draft (a pending node-parameter edit), not the stale workflowData", async () => {
        // A node-parameter edit only lands in `unsavedDraft` (BaseNode.updateParameters
        // mutates the rete model silently — no editor onChange updates workflowData).
        // onSave must PUT the draft, otherwise the saved workflow keeps the previous
        // parameter and a node test runs the stale value (the reported staleness).
        mutateAsyncMock.mockResolvedValueOnce({
            ok: true,
            data: { id: "wf-1", name: "Workflow 1", status: "DRAFT", version: "ver-2", nodes: [], edges: [] },
        });

        const staleData = { nodes: [], connections: [], __src: "workflowData" } as unknown as IEditorValue;
        const draftData = { nodes: [], connections: [], __src: "unsavedDraft" } as unknown as IEditorValue;
        useEditorStore.setState({ workflowData: staleData, unsavedDraft: draftData });

        const { default: useSaveWorkflow } = await import("./use-save-workflow");
        const { result } = renderHook(() => useSaveWorkflow());

        await act(async () => {
            await result.current.onSave("wf-1");
        });

        expect(mapWorkflowToSavePayloadMock).toHaveBeenCalledTimes(1);
        expect(mapWorkflowToSavePayloadMock).toHaveBeenCalledWith(
            expect.objectContaining({ id: "wf-1", version: "ver-1" }),
            draftData,
            "ver-1",
        );
    });

    it("stops after a canceled 409 confirmation without retrying", async () => {
        mutateAsyncMock.mockRejectedValueOnce({ response: { status: 409 } });
        showMessageBoxMock.mockResolvedValue(false);

        const { default: useSaveWorkflow } = await import("./use-save-workflow");
        const { result } = renderHook(() => useSaveWorkflow());

        await act(async () => {
            await result.current.onSave("wf-1");
        });

        expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
        expect(showMessageBoxMock).toHaveBeenCalledTimes(1);
        expect(useEditorStore.getState().isDirty).toBe(true);
        expect(toastSuccessMock).not.toHaveBeenCalled();
    });

    it("clears retry metadata when the confirmed retry also fails", async () => {
        const onSaved = rs.fn();
        const retryError = new Error("retry failed");

        mutateAsyncMock
            .mockRejectedValueOnce({ response: { status: 409 } })
            .mockRejectedValueOnce(retryError)
            .mockResolvedValueOnce({
                ok: true,
                data: {
                    id: "wf-1",
                    name: "Workflow 1",
                    status: "DRAFT",
                    version: "ver-3",
                    nodes: [],
                    edges: [],
                },
            });
        showMessageBoxMock.mockResolvedValue(true);

        const { default: useSaveWorkflow } = await import("./use-save-workflow");
        const { result } = renderHook(() => useSaveWorkflow({ onSaved }));

        await act(async () => {
            await expect(result.current.onSave("wf-1")).rejects.toThrow("retry failed");
        });

        dateNowSpy.mockReturnValueOnce(2000);

        await act(async () => {
            await result.current.onSave("wf-1");
        });

        expect(onSaved).toHaveBeenCalledTimes(1);
        expect(onSaved).toHaveBeenCalledWith({ timestamp: 2000 });
    });
});