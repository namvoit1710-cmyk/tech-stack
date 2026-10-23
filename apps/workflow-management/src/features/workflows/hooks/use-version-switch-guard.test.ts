import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import type { IEditorValue } from "@ldc/workflow-editor";
import { useEditorStore } from "../stores/editor-stores";
import { UNSAVED_VERSION_SENTINEL } from "./use-workflow-detail-page";

const showMessageBoxMock = rs.fn();
const tMock = rs.fn((key: string, params?: { version?: string }) => {
    if (key === "version_edit_warning_message") {
        return `You have unsaved changes based on ${params?.version || "{{version}}"}. Editing this version will replace them. Continue?`;
    }
    if (key === "version_edit_warning_title") {
        return "Replace Unsaved Changes?";
    }
    return key;
});

rs.mock("@/components/providers/messagebox-provider", () => ({
    useMessageBox: () => showMessageBoxMock,
}));

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: tMock }),
}));

rs.mock("../utils/version-utils", () => ({
    formatVersionLabel: (version: string | null | undefined) => version?.slice(0, 8) || "—",
}));

describe("useVersionSwitchGuard", () => {
    const mockEditorValue: IEditorValue = { nodes: [], connections: [] };

    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useEditorStore.getState().resetEditorStore();
    });

    it("allows edit when no unsavedDraft exists", async () => {
        useEditorStore.setState({
            isDirty: true,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: null,
        });

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        let canProceed = false;
        await act(async () => {
            canProceed = await result.current(mockEditorValue);
        });

        expect(canProceed).toBe(true);
        expect(showMessageBoxMock).not.toHaveBeenCalled();
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-1");
    });

    it("allows edit when no baseChangeVersion exists", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: null,
            selectedVersionId: "ver-1",
            unsavedDraft: null,
        });

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        let canProceed = false;
        await act(async () => {
            canProceed = await result.current(mockEditorValue);
        });

        expect(canProceed).toBe(true);
        expect(showMessageBoxMock).not.toHaveBeenCalled();
    });

    it("allows edit when viewing unsaved changes (selectedVersionId === UNSAVED_VERSION_SENTINEL)", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: UNSAVED_VERSION_SENTINEL,
            unsavedDraft: mockEditorValue,
        });

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        let canProceed = false;
        await act(async () => {
            canProceed = await result.current(mockEditorValue);
        });

        expect(canProceed).toBe(true);
        expect(showMessageBoxMock).not.toHaveBeenCalled();
    });

    it("shows warning on first edit when versions differ", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: mockEditorValue,
        });
        showMessageBoxMock.mockResolvedValue(true);

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        let canProceed = false;
        await act(async () => {
            canProceed = await result.current(mockEditorValue);
        });

        expect(canProceed).toBe(true);
        expect(showMessageBoxMock).toHaveBeenCalledTimes(1);
        expect(showMessageBoxMock).toHaveBeenCalledWith(
            expect.stringContaining("ver-1"),
            expect.any(String),
        );
        expect(tMock).toHaveBeenCalledWith("version_edit_warning_message", { version: "ver-1" });
        expect(tMock).toHaveBeenCalledWith("version_edit_warning_title");
    });

    it("updates baseChangeVersion after warning is confirmed", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: mockEditorValue,
        });
        showMessageBoxMock.mockResolvedValue(true);

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        await act(async () => {
            await result.current(mockEditorValue);
        });

        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-2");
    });

    it("cancels edit if warning is dismissed", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: mockEditorValue,
        });
        showMessageBoxMock.mockResolvedValue(false);

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        let canProceed = true;
        await act(async () => {
            canProceed = await result.current(mockEditorValue);
        });

        expect(canProceed).toBe(false);
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-1");
    });

    it("only warns once per version switch", async () => {
        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: mockEditorValue,
        });
        showMessageBoxMock.mockResolvedValue(true);

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result } = renderHook(() => useVersionSwitchGuard());

        // First edit - should warn
        await act(async () => {
            await result.current(mockEditorValue);
        });

        expect(showMessageBoxMock).toHaveBeenCalledTimes(1);

        // Second edit - should not warn again
        await act(async () => {
            await result.current(mockEditorValue);
        });

        expect(showMessageBoxMock).toHaveBeenCalledTimes(1);
    });

    it("warns again when switching to a different version after previous switch", async () => {
        showMessageBoxMock.mockResolvedValue(true);

        useEditorStore.setState({
            isDirty: false,
            baseChangeVersion: "ver-1",
            selectedVersionId: "ver-2",
            unsavedDraft: mockEditorValue,
        });

        const { default: useVersionSwitchGuard } = await import("./use-version-switch-guard");
        const { result, rerender } = renderHook(() => useVersionSwitchGuard());

        await act(async () => {
            await result.current(mockEditorValue);
        });

        expect(showMessageBoxMock).toHaveBeenCalledTimes(1);
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-2");

        act(() => {
            useEditorStore.setState({
                baseChangeVersion: "ver-2",
                selectedVersionId: "ver-3",
            });
        });
        rerender();

        await act(async () => {
            await result.current(mockEditorValue);
        });

        expect(showMessageBoxMock).toHaveBeenCalledTimes(2);
        expect(useEditorStore.getState().baseChangeVersion).toBe("ver-3");
    });
});
