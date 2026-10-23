import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import { useLatestRef } from "@/hooks/use-last-ref";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useEditorStore } from "../stores/editor-stores";
import type { IWorkflowSavePayload } from "../types/workflows";
import { mapWorkflowToSavePayload } from "../utils/workflow-save-mapper";
import { useUpdateWorkflow, workflowKey } from "./apis/workflows";

interface IUseSaveWorkflowOptions {
    onSaved?: (data: { timestamp: number }) => void;
}

const useSaveWorkflow = ({ onSaved }: IUseSaveWorkflowOptions = {}) => {
    const workflowData = useEditorStore(s => s.workflowData);
    const workflowInfo = useEditorStore(s => s.workflowInfo);
    const setBaselineSavePayload = useEditorStore(s => s.setBaselineSavePayload);
    const setIsDirty = useEditorStore(s => s.setIsDirty);
    const setUnsavedDraft = useEditorStore(s => s.setUnsavedDraft);
    const setUnsavedDraftBaseVersion = useEditorStore(s => s.setUnsavedDraftBaseVersion);
    const setBaseChangeVersion = useEditorStore(s => s.setBaseChangeVersion);
    const setSelectedVersionId = useEditorStore(s => s.setSelectedVersionId);
    const setWorkflowInfo = useEditorStore(s => s.setWorkflowInfo);

    const { t } = useLanguage();
    const showMessageBox = useMessageBox();
    const [, setSearchParams] = useSearchParams();
    const saveStartTimeRef = useRef<number | null>(null);
    const submittedPayloadRef = useRef<IWorkflowSavePayload | null>(null);

    const latestRef = useLatestRef({
        workflowInfo,
        workflowData,
    });

    const queryClient = useQueryClient();
    const { mutateAsync, isPending } = useUpdateWorkflow({
        onSuccess: (response) => {
            const timestamp = saveStartTimeRef.current ?? Date.now();
            const submittedPayload = submittedPayloadRef.current;
            saveStartTimeRef.current = null;
            submittedPayloadRef.current = null;

            setWorkflowInfo(response.data);
            setBaselineSavePayload(submittedPayload);
            setBaseChangeVersion(response.data.version ?? null);
            setSelectedVersionId(response.data.version ?? null);
            setIsDirty(false);
            setUnsavedDraft(null);
            setUnsavedDraftBaseVersion(null);

            // Pin the just-saved version in the URL so a reload (F5) re-loads
            // exactly this version instead of re-guessing "latest".
            const savedVersion = response.data.version;
            if (savedVersion) {
                setSearchParams((prev) => {
                    const next = new URLSearchParams(prev);
                    next.set("version", savedVersion);
                    return next;
                }, { replace: true });
            }

            void queryClient.invalidateQueries({ queryKey: workflowKey.all })
            toast.success(t("notification.success"), t("notification.workflow_saved_successfully"))
            onSaved?.({ timestamp });
        }
    })

    const onSave = useCallback(async (workflowId: string, workflowName?: string) => {
        const { workflowInfo, workflowData } = latestRef.current;
        if (!workflowInfo) {
            return;
        }

        // Read the pending edit from the store at call time (not the render-time
        // latestRef) so a save triggered synchronously right after a parameter
        // flush still sees the just-committed values. Crucially, a node-parameter
        // edit only lands in `unsavedDraft`: BaseNode.updateParameters mutates the
        // rete model silently (it emits no editor onChange), so `workflowData`
        // never receives it. Saving `workflowData` here would PUT stale
        // parameters and the node test would keep running the previous value
        // (the reported staleness). Prefer the unsaved draft; fall back to
        // workflowData when there is no pending edit.
        const { unsavedDraft, baseChangeVersion } = useEditorStore.getState();
        const dataToSave = unsavedDraft ?? workflowData;

        if (!baseChangeVersion) {
            toast.error(t("notification.error"), t("version_save_error_no_base"));
            return;
        }

        const payload: IWorkflowSavePayload = mapWorkflowToSavePayload({ ...workflowInfo, name: workflowName ?? workflowInfo.name }, dataToSave, baseChangeVersion)

        saveStartTimeRef.current = Date.now();
        submittedPayloadRef.current = payload;

        try {
            await mutateAsync({ workflowId, payload })
        } catch (error) {
            const statusCode = typeof error === "object" && error !== null && "response" in error
                ? (error.response as { status?: number } | undefined)?.status
                : undefined;

            if (statusCode !== 409) {
                saveStartTimeRef.current = null;
                submittedPayloadRef.current = null;
                throw error;
            }

            const confirmed = await showMessageBox(
                t("workflow_save_conflict_message"),
                t("workflow_save_conflict_title"),
            );

            if (!confirmed) {
                saveStartTimeRef.current = null;
                submittedPayloadRef.current = null;
                return;
            }

            try {
                await mutateAsync({ workflowId, payload: { ...payload, is_confirm: true } })
            } catch (retryError) {
                saveStartTimeRef.current = null;
                submittedPayloadRef.current = null;
                throw retryError;
            }
        }
    }, [latestRef, mutateAsync, showMessageBox, t])

    return { onSave, isSaving: isPending }
}

export default useSaveWorkflow;