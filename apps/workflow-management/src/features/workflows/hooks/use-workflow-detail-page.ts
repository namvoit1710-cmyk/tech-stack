import api from "@/lib/api";
import { useQuery, useQueryClient } from "@ldc/tanstack-query";
import type { WorkflowEditorHandle } from "@ldc/workflow-editor";
import type { RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useEditorStore } from "../stores/editor-stores";
import { useUIPanelStore } from "../stores/ui-panel-stores";
import type { IWorkflowDetailResponse } from "../types/workflows";
import { mapApiToWorkflowValue } from "../utils/node-mapper-utils";
import { mapWorkflowToSavePayload } from "../utils/workflow-save-mapper";
import { useWorkflowPublished, workflowKey } from "./apis/workflows";

export const UNSAVED_VERSION_SENTINEL = "__unsaved__";

export function useWorkflowDetail(
    workflowId: string,
    editorRef?: RefObject<WorkflowEditorHandle | null>,
    editorSettledRef?: RefObject<boolean>,
    settleTimerRef?: RefObject<ReturnType<typeof setTimeout> | null>,
    suppressGuardRef?: RefObject<boolean>,
) {
    const queryClient = useQueryClient();

    const workflowInfo = useEditorStore((s) => s.workflowInfo);
    const selectedVersionId = useEditorStore((s) => s.selectedVersionId);
    const isDirty = useEditorStore((s) => s.isDirty);
    const unsavedDraft = useEditorStore((s) => s.unsavedDraft);
    const baseChangeVersion = useEditorStore((s) => s.baseChangeVersion);
    const unsavedDraftBaseVersion = useEditorStore((s) => s.unsavedDraftBaseVersion);
    const setWorkflowInfo = useEditorStore((s) => s.setWorkflowInfo);
    const setSelectedVersionId = useEditorStore((s) => s.setSelectedVersionId);
    const setBaselineSavePayload = useEditorStore((s) => s.setBaselineSavePayload);
    const setIsDirty = useEditorStore((s) => s.setIsDirty);
    const setWorkflowData = useEditorStore((s) => s.setWorkflowData);
    const resetEditorStore = useEditorStore((s) => s.resetEditorStore);
    const setBaseChangeVersion = useEditorStore((s) => s.setBaseChangeVersion);
    const setUnsavedDraftBaseVersion = useEditorStore((s) => s.setUnsavedDraftBaseVersion);

    const setIsSwitchingVersion = useUIPanelStore((s) => s.setIsSwitchingVersion);
    const isSwitchingVersion = useUIPanelStore((s) => s.isSwitchingVersion);

    // Stable ref so switchVersion callback doesn't re-create on every store update
    const storeRef = useRef({ isDirty, selectedVersionId, unsavedDraft, baseChangeVersion, unsavedDraftBaseVersion });
    storeRef.current = { isDirty, selectedVersionId, unsavedDraft, baseChangeVersion, unsavedDraftBaseVersion };

    const [searchParams, setSearchParams] = useSearchParams();
    // Pin the version selected at mount so a reload (F5) re-loads exactly that
    // version instead of re-guessing "latest". Captured into state — NOT reactive
    // to later ?version writes — so save/switch (which update the URL for the NEXT
    // reload) don't swap the active query mid-session.
    const [pinnedVersion, setPinnedVersion] = useState<string | null>(() => {
        const v = searchParams.get("version");
        return v && v !== UNSAVED_VERSION_SENTINEL ? v : null;
    });
    const isPinned = !!pinnedVersion;

    // SA-2252 — default (no pinned ?version) load is published-preferred: opening a
    // workflow shows its PUBLISHED version (falls back to latest-edited when nothing is
    // published yet). A specific draft/version is still reachable via the version picker
    // (which pins ?version and loads the exact version below).
    const { data: latestResponse, isLoading: isLatestLoading } = useWorkflowPublished(workflowId, {
        enabled: !!workflowId && !isPinned,
    });
    const pinnedQuery = useQuery({
        queryKey: workflowKey.getWorkflowByVersion(workflowId, pinnedVersion ?? ""),
        queryFn: (): Promise<IWorkflowDetailResponse> =>
            api.get(`/workflows/${workflowId}/version/${pinnedVersion}`),
        enabled: !!workflowId && isPinned,
    });
    const response = isPinned ? pinnedQuery.data : latestResponse;
    const isLoading = isPinned ? pinnedQuery.isLoading : isLatestLoading;

    // A stale/invalid ?version (e.g. a deleted version) → drop the pin, load latest.
    useEffect(() => {
        if (isPinned && pinnedQuery.isError) {
            setPinnedVersion(null);
            setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.delete("version");
                return next;
            }, { replace: true });
        }
    }, [isPinned, pinnedQuery.isError, setSearchParams]);

    const resetSettle = useCallback(() => {
        if (settleTimerRef?.current) clearTimeout(settleTimerRef.current);
        if (editorSettledRef) editorSettledRef.current = false;
    }, []);

    useEffect(() => {
        if (response?.data) {
            const shouldKeepExplicitVersionSelection = !!workflowInfo
                && !!selectedVersionId
                && selectedVersionId !== response.data.version;

            if (shouldKeepExplicitVersionSelection || isDirty) {
                return;
            }

            const workflowData = mapApiToWorkflowValue(response.data);
            const baselinePayload = mapWorkflowToSavePayload(response.data, workflowData, response.data.version ?? "");

            resetSettle();
            setWorkflowData(workflowData);
            setBaseChangeVersion(response.data.version ?? null);
            setUnsavedDraftBaseVersion(null);
            setBaselineSavePayload(baselinePayload);

            setWorkflowInfo(response.data);
            setSelectedVersionId(response.data.version ?? null);
            setIsDirty(false);
        }
    }, [response, selectedVersionId, isDirty, setBaselineSavePayload, setIsDirty, setSelectedVersionId, setWorkflowData, setWorkflowInfo, workflowInfo, resetSettle]);

    useEffect(() => {
        return () => resetEditorStore();
    }, [resetEditorStore]);

    const switchVersion = useCallback(async (nextVersionId: string) => {
        const { selectedVersionId, unsavedDraft, baseChangeVersion, unsavedDraftBaseVersion } = storeRef.current;

        if (nextVersionId === selectedVersionId) {
            return;
        }

        if (nextVersionId === UNSAVED_VERSION_SENTINEL) {
            if (unsavedDraft) {
                if (editorRef?.current?.removeAllNodes) {
                    resetSettle();
                    await editorRef.current.removeAllNodes();
                }
                setWorkflowData(unsavedDraft);
                setBaseChangeVersion(unsavedDraftBaseVersion ?? baseChangeVersion ?? null);
                setIsDirty(true);
            }
            setSelectedVersionId(UNSAVED_VERSION_SENTINEL);
            // An unsaved draft isn't reloadable — clear the pin so a reload loads latest.
            setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.delete("version");
                return next;
            }, { replace: true });
            return;
        }

        if (unsavedDraft) {
            setUnsavedDraftBaseVersion(baseChangeVersion ?? unsavedDraftBaseVersion ?? null);
        }

        setIsSwitchingVersion(true);
        if (suppressGuardRef) suppressGuardRef.current = true;
        try {
            const response = await queryClient.fetchQuery({
                queryKey: workflowKey.getWorkflowByVersion(workflowId, nextVersionId),
                queryFn: (): Promise<IWorkflowDetailResponse> =>
                    api.get(`/workflows/${workflowId}/version/${nextVersionId}`),
            });
            const versionWorkflow = response.data;
            const nextWorkflowData = mapApiToWorkflowValue(versionWorkflow);
            const nextBaselineSavePayload = mapWorkflowToSavePayload(versionWorkflow, nextWorkflowData, versionWorkflow.version ?? "");

            if (editorRef?.current?.removeAllNodes) {
                resetSettle();
                await editorRef.current.removeAllNodes();
            }

            setWorkflowInfo(versionWorkflow);
            setWorkflowData(nextWorkflowData);
            setBaselineSavePayload(nextBaselineSavePayload);
            setSelectedVersionId(nextVersionId);
            setIsDirty(false);
            // Pin the switched-to version for the next reload.
            setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set("version", nextVersionId);
                return next;
            }, { replace: true });
            // unsavedDraft survives — visible in version picker via !!unsavedDraft
        } finally {
            setIsSwitchingVersion(false);
            // Keep guard suppressed until the editor finishes re-rendering after the version load
            setTimeout(() => {
                if (suppressGuardRef) suppressGuardRef.current = false;
            }, 300);
        }
    }, [queryClient, workflowId, setWorkflowData, setSelectedVersionId, setWorkflowInfo, setBaselineSavePayload, setIsDirty, editorRef, resetSettle, setBaseChangeVersion, setUnsavedDraftBaseVersion, setSearchParams]);

    return {
        workflow: workflowInfo,
        isLoading: isLoading || isSwitchingVersion,
        switchVersion,
    };
}
