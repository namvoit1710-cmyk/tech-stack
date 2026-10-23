import Page from "@/components/containers/page";
import { SERVICE_CONFIGS } from "@/constants/config";
import { EXECUTION_STATUS, LOG_ACTION } from "@/constants/log";
import LoadingOverlay from "@/features/workflows/components/loading-overlay/loading-overlay";
import { InteractionModalWrapper } from "@/features/workflows/components/modals/interaction-modal/interaction-modal-wrapper";
import NodeEditInfoModal from "@/features/workflows/components/modals/node-edit-infor-modal";
import NodesDetailModal from "@/features/workflows/components/modals/nodes-detail-modal/detail-modal";
import NodePalette from "@/features/workflows/components/node-palette/node-palette";
import NodePaletteContent from "@/features/workflows/components/node-palette/palette-content";
import WorkflowDetailHeaderAction from "@/features/workflows/components/workflow-header-action/workflow-header-action";
import LogSection from "@/features/workflows/components/workflow-logs/logs";
import { WorkflowVersionSheet } from "@/features/workflows/components/workflow-version-sheet";
import { usePublishWorkflow, workflowKey } from "@/features/workflows/hooks/apis/workflows";
import useExecuteWorkflowV2 from "@/features/workflows/hooks/use-execute-workflow-v2";
import useSaveWorkflow from "@/features/workflows/hooks/use-save-workflow";
import useSelectNode from "@/features/workflows/hooks/use-select-node";
import useVersionSwitchGuard from "@/features/workflows/hooks/use-version-switch-guard";
import { UNSAVED_VERSION_SENTINEL, useWorkflowDetail } from "@/features/workflows/hooks/use-workflow-detail-page";
import { useWorkflowLogSync } from "@/features/workflows/hooks/use-workflow-log-sync";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useUIPanelStore } from "@/features/workflows/stores/ui-panel-stores";
import type { INodePallete } from "@/features/workflows/types/node-pallete";
import type { IWorkflow } from "@/features/workflows/types/workflows";
import { mapNodeToEditorNode } from "@/features/workflows/utils/node-mapper-utils";
import { useBuilderServices } from "@ldc/autoform";
import { useInteractionModal } from "@ldc/form-tool";
import { useQueryClient } from "@ldc/tanstack-query";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import type { BaseNode, IEditorValue, WorkflowEditorHandle } from "@ldc/workflow-editor";
import { WorkflowEditor } from "@ldc/workflow-editor";
import { isEqual } from "lodash-es";
import { useCallback, useRef } from "react";
import { useParams } from "react-router-dom";

const WorkflowDetailPage = () => {
    const { workflowId } = useParams()

    const workflowEditorRef = useRef<WorkflowEditorHandle | null>(null);
    const queryClient = useQueryClient();

    const editorSettledRef = useRef(false);
    const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const { workflow, isLoading, switchVersion } = useWorkflowDetail(workflowId!, workflowEditorRef, editorSettledRef, settleTimerRef)

    const { addLightLog, addExecutionLog } = useWorkflowLogSync(workflowId!)

    const services = useBuilderServices(SERVICE_CONFIGS);
    const { modalState, prompt, dismiss } = useInteractionModal();

    const { onSave, isSaving } = useSaveWorkflow({
        onSaved: ({ timestamp }) =>
            addExecutionLog(LOG_ACTION.WORKFLOW_SAVED, { status: EXECUTION_STATUS.SUCCESS, timestamp }),
    })

    const {
        onRunNode,
        onRunWorkflow,
        stopWorkflow,
        isRunNodeLoading,
        loadingNodeId,
        isRunWorkflowExecuting,
        isPrepareWorkflowExecuting
    } = useExecuteWorkflowV2({
        prompt,
        dismiss,
        editorRef: workflowEditorRef,

        // FOR LOGS
        onNodeCompleted: ({ nodeId, nodeName, timestamp }) =>
            addExecutionLog(LOG_ACTION.NODE_EXECUTED, { status: EXECUTION_STATUS.SUCCESS, nodeId, nodeName, timestamp }),
        onNodeFailed: ({ nodeId, nodeName, timestamp, errorMessage }) =>
            addExecutionLog(LOG_ACTION.NODE_EXECUTED, { status: EXECUTION_STATUS.ERROR, nodeId, nodeName, timestamp, errorMessage }),
        onWorkflowCompleted: ({ timestamp }) =>
            addExecutionLog(LOG_ACTION.WORKFLOW_EXECUTED, { status: EXECUTION_STATUS.SUCCESS, timestamp }),
        onWorkflowFailed: ({ timestamp, errorMessage }) =>
            addExecutionLog(LOG_ACTION.WORKFLOW_EXECUTED, { status: EXECUTION_STATUS.ERROR, timestamp, errorMessage }),
    })

    const workflowData = useEditorStore((s) => s.workflowData);
    const workflowInfo = useEditorStore((s) => s.workflowInfo);
    const selectedVersionId = useEditorStore((s) => s.selectedVersionId);
    const setSelectedVersionId = useEditorStore((s) => s.setSelectedVersionId);
    const setIsDirty = useEditorStore((s) => s.setIsDirty);
    const setWorkflowData = useEditorStore((s) => s.setWorkflowData);
    const setUnsavedDraft = useEditorStore((s) => s.setUnsavedDraft);
    const setUnsavedDraftBaseVersion = useEditorStore((s) => s.setUnsavedDraftBaseVersion);

    const changeContextRef = useRef<{ workflowData: IEditorValue; workflowInfo: IWorkflow | null }>({ workflowData, workflowInfo });
    changeContextRef.current = { workflowData, workflowInfo };

    const { mutateAsync: publishWorkflow, isPending: isPublishing } = usePublishWorkflow({
        onSuccess: async () => {
            await queryClient.invalidateQueries({ queryKey: workflowKey.getWorkflowVersions(workflowId!) });
            await queryClient.invalidateQueries({ queryKey: workflowKey.getWorkflowById(workflowId!) });
        },
    });

    const setIsVersionSheetOpen = useUIPanelStore((s) => s.setIsVersionSheetOpen);

    const setIsOpenNodesPopup = useUIPanelStore((s) => s.setIsOpenNodesPopup);
    const isOpenNodeListPanel = useUIPanelStore((s) => s.isOpenNodeListPanel);
    const toggleNodeListPanel = useUIPanelStore((s) => s.toggleNodeListPanel);
    const setIsOpenNodeInfoEditorModal = useUIPanelStore((s) => s.setIsOpenNodeInfoEditorModal);

    const { onSelectedNode } = useSelectNode(workflowEditorRef);
    const guardEdit = useVersionSwitchGuard();

    const openNodePopup = useCallback((node: BaseNode) => {
        setIsOpenNodesPopup(true);
        onSelectedNode(node);
    }, []);

    const handleNodeSelected = useCallback((node: BaseNode) => {
        onSelectedNode(node);
    }, []);

    const handleEditNode = useCallback((node: BaseNode) => {
        onSelectedNode(node);
        setIsOpenNodeInfoEditorModal(true);
    }, [])

    const handleChange = useCallback(async (value: IEditorValue) => {
        const { workflowInfo } = changeContextRef.current;
        if (!workflowInfo) return;

        if (!editorSettledRef.current) {
            setWorkflowData(value);
            if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
            settleTimerRef.current = setTimeout(() => {
                editorSettledRef.current = true;
            }, 400);
            return;
        }

        const { workflowData: baselineData } = changeContextRef.current;
        setWorkflowData(value);

        const dirty = !isEqual(value, baselineData);
        setIsDirty(dirty);

        if (dirty) {
            const version = workflowInfo.version ?? null;
            setUnsavedDraft(value);
            setSelectedVersionId(UNSAVED_VERSION_SENTINEL);
            useEditorStore.getState().setBaseChangeVersion(version);
            setUnsavedDraftBaseVersion(version);
        }
    }, [guardEdit, setIsDirty, setUnsavedDraft, setUnsavedDraftBaseVersion, setWorkflowData]);

    const handleParametersChanged = useCallback(() => {
        const workflowInfo = changeContextRef.current.workflowInfo;
        if (!workflowInfo) return;

        const freshValue = workflowEditorRef.current?.getSerializedValue?.();
        if (!freshValue) return;

        setIsDirty(true);
        setUnsavedDraft(freshValue);
        setUnsavedDraftBaseVersion(workflowInfo.version ?? null);
        setSelectedVersionId(UNSAVED_VERSION_SENTINEL);
        useEditorStore.getState().setBaseChangeVersion(workflowInfo.version ?? null);
    }, [setIsDirty, setUnsavedDraft, selectedVersionId, setUnsavedDraftBaseVersion]);

    const handlePublish = useCallback(async () => {
        await publishWorkflow({
            workflowId: workflowId!,
            payload: selectedVersionId ? { version: selectedVersionId, is_confirm: true } : undefined,
        });
    }, [publishWorkflow, selectedVersionId, workflowId]);

    const handleAddNodeFromList = useCallback((nodePallete: INodePallete) => {
        const editorNode = mapNodeToEditorNode(nodePallete);
        workflowEditorRef.current?.addNode?.(editorNode);
    }, []);

    const handleAddNodeClick = useCallback(() => {
        toggleNodeListPanel();
    }, []);

    const removeConnection = useCallback(({ sourceId, targetId }: { sourceId?: string, targetId?: string }) => {
        if (sourceId) {
            workflowEditorRef.current?.removeConnectionBySource?.(sourceId);
        }
        if (targetId) {
            workflowEditorRef.current?.removeConnectionByTarget?.(targetId);
        }
    }, []);

    const updateNodeView = useCallback((nodeId: string) => {
        workflowEditorRef.current?.updateNodeView?.(nodeId);
    }, []);

    const handleRunNode = useCallback(async (nodeId: string) => {
        // Read the sentinel from the store, not the render-time closure: the
        // Execute button flushes the parameter debounce synchronously just
        // before this runs, marking the draft unsaved in the store within the
        // same tick — before React re-renders this callback's closure. Awaiting
        // the save guarantees the node test dispatches against the freshly
        // persisted parameters (fixes the "output doesn't change" staleness).
        if (useEditorStore.getState().selectedVersionId === UNSAVED_VERSION_SENTINEL) {
            await onSave(workflowId!);
        }
        onRunNode(nodeId);
    }, [onRunNode, onSave, workflowId]);

    const handleRunWorkflow = useCallback(async () => {
        if (useEditorStore.getState().selectedVersionId === UNSAVED_VERSION_SENTINEL) {
            await onSave(workflowId!);
        }

        onRunWorkflow();
    }, [onRunWorkflow, onSave, workflowId]);

    return (
        <>
            <Page className="gap-0">
                <Page.Header
                    title={(
                        workflow?.name && (<div className="flex items-start gap-2">
                            <span>{workflow?.name}</span>
                            <Badge variant={workflow?.main_flow ? "default" : "outline"} className="whitespace-nowrap mt-0.5">{workflow?.main_flow ? "Main Flow" : "Sub Flow"}</Badge>
                        </div>)
                    )}
                    description={workflow?.description}
                    className="border-b"
                    actions={
                        <WorkflowDetailHeaderAction
                            workflowId={workflowId!}
                            workflowTitle={workflow?.name ?? ""}
                            isPublishing={isPublishing}
                            isSaving={isSaving}
                            isPrepareWorkflowExecuting={isPrepareWorkflowExecuting}
                            isRunWorkflowExecuting={isRunWorkflowExecuting}
                            onExecute={handleRunWorkflow}
                            onVersionChange={switchVersion}
                            onVersionSheetOpen={() => setIsVersionSheetOpen(true)}
                            onPublish={handlePublish}
                            onStop={stopWorkflow}
                            onSave={(workflowName) => onSave(workflowId!, workflowName)}
                        />
                    }
                />
                <div
                    className={cn(
                        "relative",
                        "flex flex-col",
                        "overflow-hidden h-full w-full",
                    )}
                >
                    <div className={cn("flex-2 overflow-hidden relative", isSaving && "pointer-events-none opacity-90")}>
                        <WorkflowEditor
                            value={workflowData}
                            ref={workflowEditorRef}
                            onAddNode={handleAddNodeClick}
                            onChange={handleChange}
                            onExecuteNode={(nodeId: string) => handleRunNode(nodeId)}
                            onOpenNodePopup={openNodePopup}
                            onNodeSelected={handleNodeSelected}
                            onNodeAdded={() => addLightLog("NODE_ADDED")}
                            onConnectionAdded={() => addLightLog("CONNECTION_ADDED")}
                            onEditNode={handleEditNode}
                        />

                        <NodePalette
                            side="right"
                            isOpen={isOpenNodeListPanel}
                            onClose={toggleNodeListPanel}
                        >
                            <NodePaletteContent
                                onCloseDrawer={toggleNodeListPanel}
                                onSelectNode={handleAddNodeFromList}
                            />
                        </NodePalette>
                    </div>

                    <LogSection workflowId={workflowId!} />

                    <LoadingOverlay isLoading={isLoading} />
                </div>
            </Page>

            <NodesDetailModal
                onExecuteNode={(nodeId: string) => handleRunNode(nodeId)}
                onSelectNode={handleNodeSelected}
                removeConnection={removeConnection}
                updateNodeView={updateNodeView}
                onParametersChanged={handleParametersChanged}

                isRunNodeLoading={isRunNodeLoading}
                loadingNodeId={loadingNodeId}
            />

            <InteractionModalWrapper
                modalState={modalState}
                services={services}
            />

            <NodeEditInfoModal />

            <WorkflowVersionSheet
                workflowId={workflowId!}
                onVersionChange={switchVersion}
            />
        </>
    );
};

export default WorkflowDetailPage;