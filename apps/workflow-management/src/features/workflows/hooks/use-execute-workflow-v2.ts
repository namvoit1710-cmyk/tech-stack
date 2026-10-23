import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import { useLatestRef } from "@/hooks/use-last-ref";
import { pushGatewaySocket } from "@/lib/socket";
import type { InteractionResultType, IRequestedPayload } from "@ldc/form-tool";
import { createConfirmRejectPayload, InteractionModalEnum } from "@ldc/form-tool";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import type { WorkflowEditorHandle } from "@ldc/workflow-editor";
import type { RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { workflowInputData } from "../config/workflow-input-data";
import { useEditorStore } from "../stores/editor-stores";
import type { SocketEventPayloadMap } from "../types/socket-event";
import { SocketEvent } from "../types/socket-event";
import type { INodeLogData, IWorkflowLogData } from "../types/workflow-log";
import { getSocketEventName, getSocketEventPayload, normalizeSocketPayload } from "../utils/socket-data-chunk";
import { usePatchNodeData, useRunNode, useRunWorkflow, useStopWorkflow, useSubmitNodeData, workflowKey } from "./apis/workflows";

interface IUseExecuteWorkflowProps {
    dismiss?: (nodeId: string) => void;
    prompt: (payload: IRequestedPayload) => Promise<InteractionResultType>;
    editorRef: RefObject<WorkflowEditorHandle | null>;
    onSaveWorkflow?: () => void;
    onNodeCompleted?: (data: INodeLogData) => void;
    onNodeFailed?: (data: INodeLogData) => void;
    onWorkflowCompleted?: (data: IWorkflowLogData) => void;
    onWorkflowFailed?: (data: IWorkflowLogData) => void;
}

const useExecuteWorkflow = ({
    prompt,
    dismiss,
    editorRef,
    onSaveWorkflow,
    onNodeCompleted,
    onNodeFailed,
    onWorkflowCompleted,
    onWorkflowFailed
}: IUseExecuteWorkflowProps) => {
    const { t } = useLanguage();

    const localExecution = useEditorStore((s) => s.localExecution);
    const setLocalExecution = useEditorStore((s) => s.setLocalExecution);
    const setNodeExecution = useEditorStore((s) => s.setNodeExecution);

    const [isWorkflowRunning, setIsWorkflowRunning] = useState(false);

    const workflowInfo = useEditorStore((s) => s.workflowInfo);
    const workflowId = workflowInfo?.id;
    const testRunId = workflowInfo?.test_run_id;

    const runToastIdRef = useRef<string | number | null>(null);

    const workflowStartTimeRef = useRef<number | null>(null);

    const getNodeName = useCallback((nodeId: string): string | undefined => {
        const foundNode = useEditorStore
            .getState()
            .workflowData.nodes.find((n) => n.id === nodeId);
        return foundNode?.data?.title ?? foundNode?.data?.name;
    }, []);

    const resetRunToast = useCallback(() => {
        if (runToastIdRef.current) {
            setIsWorkflowRunning(false);
            toast.dismiss(runToastIdRef.current);
            runToastIdRef.current = null;
        }
    }, []);

    const dismissRunToast = useCallback((type: "success" | "error") => {
        resetRunToast();

        if (type === "success") {
            toast.success(
                t("notification.success"),
                t("notification.workflow_completed_successfully")
            );
        } else {
            toast.error(
                t("notification.error"),
                t("notification.workflow_execution_failed")
            );
        }
    }, []);

    const latestRef = useLatestRef({
        testRunId,
        workflowId,
        prompt,
        dismiss,

        localExecution,
        setLocalExecution,

        onSaveWorkflow,
        onNodeCompleted,
        onNodeFailed,
        onWorkflowCompleted,
        onWorkflowFailed
    });

    const {
        mutate: runNode,
        isPending: isRunNodeLoading,
        variables: runNodeVariables
    } = useRunNode({
        onError: (_, { nodeId }) => {
            latestRef.current.onNodeFailed?.({
                nodeId: nodeId,
                nodeName: getNodeName(nodeId),
                timestamp: Date.now()
            });
            resetRunToast();
        }
    });
    const onRunNode = useCallback(
        (nodeId: string) => {
            editorRef.current?.updateNodeConnectionStatus?.(nodeId, "idle");

            const workflowId = latestRef.current.workflowId;
            if (!workflowId) return;

            latestRef.current.onSaveWorkflow?.();
            setLocalExecution({ type: "node", nodeId });
            runNode({ workflowId, nodeId });
        },
        [runNode, setLocalExecution]
    );

    const queryClient = useQueryClient();
    const { mutate: stopWorkflow } = useStopWorkflow({
        onSuccess: () => {
            resetRunToast();
            editorRef.current?.resetAllNodeStatus?.();
        }
    });

    const handleStopWorkflow = useCallback(() => {
        const testRunId = latestRef.current.testRunId;
        if (!testRunId) return;
        stopWorkflow(testRunId);
    }, [testRunId]);

    const { mutate: runWorkflow, isPending: isRunWorkflowLoading } =
        useRunWorkflow({
            onSuccess: () => {
                if (!testRunId) {
                    void queryClient.invalidateQueries({
                        queryKey: workflowKey.getWorkflowById(workflowId!)
                    });
                }
            },
            onError: () => {
                latestRef.current.onWorkflowFailed?.({ timestamp: Date.now() });
                resetRunToast();
            }
        });

    const onRunWorkflow = useCallback(async () => {
        editorRef.current?.resetAllNodeStatus?.();

        const formValue = await latestRef.current.prompt({
            type: InteractionModalEnum.WORKFLOW_EXECUTION,
            input_schema: workflowInputData
        });

        let inputDataObj: Record<string, unknown> = {};
        try {
            if (!formValue) return;

            const inputData = (formValue as Record<"input_data", string>).input_data as string | undefined;

            if (!inputData) return

            if (typeof inputData === "string") {
                inputDataObj = JSON.parse(inputData);
            }
        } catch (_error) {
            console.error("Failed to parse workflow input data", _error);
            inputDataObj = {};
        }

        setLocalExecution({ type: "workflow", runId: testRunId });
        setIsWorkflowRunning(true);
        resetRunToast();

        runToastIdRef.current ??= toast.loading(
            t("notification.loading"),
            t("notification.workflow_executing")
        );

        workflowStartTimeRef.current = Date.now();
        // SA-2365: bind the selected environment to this run (if any).
        const environmentId = useEditorStore.getState().selectedEnvironmentId ?? undefined;
        runWorkflow({ workflowId: workflowId!, config: { input_data: inputDataObj, environment_id: environmentId } });
        return;
    }, [workflowId]);

    const { mutateAsync: submitInteractionResult } = useSubmitNodeData();
    const { mutateAsync: patchInteractionResult } = usePatchNodeData();

    const showMessageBox = useMessageBox();

    useEffect(() => {
        if (!testRunId) return;

        const reRunNode = async ({ nodeId, runId, taskId, eTag, payload }: {
            nodeId: string;
            runId: string;
            taskId: string;
            eTag: string;
            payload: Record<string, unknown>;
        }) => {

            await patchInteractionResult({
                node_id: nodeId,
                run_id: runId,
                task_id: taskId,
                etag: eTag,
                data: payload
            })


            await submitInteractionResult({
                runId,
                nodeId,
                taskId,
            })
        }

        const socket = pushGatewaySocket.connect("/v1/pushgateway");

        if (socket.connected) {
            socket.emit("wait", { key: `run:${testRunId}` });
        }

        const handleConnect = () => {
            socket.emit("wait", { key: `run:${testRunId}` });
        }

        const handleReconnectFailed = () => async () => {
            const result = await showMessageBox(t("the_system_disconnected_please_reload_to_connect_again"), t("disconnected_server_warning"))

            if (result) {
                window.location.reload();
            }
        }
        socket.on("connect", handleConnect)
        socket.on("reconnect", handleConnect);
        socket.on("reconnect_failed", handleReconnectFailed)

        const onRunStarted = () => {
            if (!latestRef.current.localExecution) return;

            if (latestRef.current.localExecution?.type === "workflow") {
                runToastIdRef.current ??= toast.loading(
                    t("notification.loading"),
                    t("notification.workflow_executing")
                );
            }
            workflowStartTimeRef.current = Date.now();
        };

        const onRunCompleted = (payload: SocketEventPayloadMap[SocketEvent.RunStatus]) => {
            if (!latestRef.current.localExecution) return;

            workflowStartTimeRef.current = null;
            const timestamp = new Date().getTime();
            latestRef.current.onWorkflowCompleted?.({ timestamp });

            if (latestRef.current.localExecution?.type === "workflow" && latestRef.current.localExecution.runId === payload.run_id) {
                setLocalExecution(null);
                dismissRunToast("success");
                editorRef.current?.zoomToFit?.();
            }

        };

        const onRunFailed = (payload: SocketEventPayloadMap[SocketEvent.RunStatus]) => {
            if (!latestRef.current.localExecution) return;

            workflowStartTimeRef.current = null;
            const timestamp = new Date().getTime();
            latestRef.current.onWorkflowFailed?.({ timestamp, errorMessage: payload.error });

            if (latestRef.current.localExecution?.type === "workflow" && latestRef.current.localExecution.runId === payload.run_id) {
                setLocalExecution(null);
                dismissRunToast("error");
            }
        };

        const onRunChildRunStarted = (payload: SocketEventPayloadMap[SocketEvent.NodeDispatched]) => {
            if (!latestRef.current.localExecution) return;

            console.log("Child run started", payload);
        };

        const onRunChildRunCompleted = (payload: SocketEventPayloadMap[SocketEvent.NodeCompleted]) => {
            if (!latestRef.current.localExecution) return;

            console.log("Child run completed", payload);
        }

        const onTaskDispatched = async (payload: SocketEventPayloadMap[SocketEvent.NodeDispatched]) => {
            if (!latestRef.current.localExecution) return;

            setNodeExecution(payload.node_id, {
                status: "executing",
                cacheKey: payload.idempotency_key,
                timestamp: new Date().getTime()
            });

            if (latestRef.current.localExecution?.type === "workflow" && !!editorRef.current?.getZoomLevel?.() && editorRef.current.getZoomLevel() < 1) {
                await editorRef.current?.zoomByLevel?.(1);
            }

            if (!editorRef.current?.isNodeInViewport?.(payload.node_id)) {
                await editorRef.current?.centerOnNode?.(payload.node_id);
            }

            editorRef.current?.setNodeStatus?.(payload.node_id, "executing");
        };

        const onTaskCompleted = (payload: SocketEventPayloadMap[SocketEvent.NodeCompleted]) => {
            if (!latestRef.current.localExecution) return;

            const timestamp = new Date().getTime();

            latestRef.current.dismiss?.(payload.node_id);

            setNodeExecution(payload.node_id, {
                status: "completed",
                runId: payload.run_id,
                cacheKey: "",
                timestamp,
                output: undefined,
                hasLargeOutput: false
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "completed");
            latestRef.current.onNodeCompleted?.({
                nodeId: payload.node_id,
                nodeName: getNodeName(payload.node_id),
                timestamp
            });

            if (latestRef.current.localExecution?.type === "node" && latestRef.current.localExecution.nodeId === payload.node_id) {
                setLocalExecution(null);
                resetRunToast();
            }

            if (!runToastIdRef.current) {
                toast.success(
                    t("notification.success"),
                    t("notification.node_completed_successfully")
                );
            }
        };

        const onTaskFailed = (payload: SocketEventPayloadMap[SocketEvent.NodeFailed]) => {
            if (!latestRef.current.localExecution) return;

            const timestamp = new Date().getTime();

            latestRef.current.dismiss?.(payload.node_id);

            setNodeExecution(payload.node_id, {
                status: "failed",
                runId: payload.run_id,
                cacheKey: "",
                timestamp,
                output: undefined,
                hasLargeOutput: false
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "failed");

            latestRef.current.onNodeFailed?.({
                nodeId: payload.node_id,
                nodeName: getNodeName(payload.node_id),
                timestamp,
                errorMessage: payload.error_message
            });

            if (latestRef.current.localExecution?.type === "node" && latestRef.current.localExecution.nodeId === payload.node_id) {
                setLocalExecution(null);
                resetRunToast();
            }

            if (!runToastIdRef.current) {
                toast.error(
                    t("notification.error"),
                    t("notification.node_execution_failed")
                );
            }
        };

        const onTaskCancelled = (payload: SocketEventPayloadMap[SocketEvent.NodeCancelled]) => {
            if (!latestRef.current.localExecution) return;

            const timestamp = new Date().getTime();

            latestRef.current.dismiss?.(payload.node_id);

            setNodeExecution(payload.node_id, {
                status: "idle",
                runId: payload.run_id,
                cacheKey: "",
                timestamp,
                output: undefined,
                hasLargeOutput: false
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "idle");

            if (latestRef.current.localExecution?.type === "node" && latestRef.current.localExecution.nodeId === payload.node_id) {
                setLocalExecution(null);
                resetRunToast();
            }
        };

        const onInputRequested = async (payload: SocketEventPayloadMap[SocketEvent.InputRequested]) => {
            if (!latestRef.current.localExecution) return;

            console.log("Input requested", payload);

            setNodeExecution(payload.node_id, {
                status: "executing",
                timestamp: new Date().getTime()
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "executing");

            const result = await latestRef.current.prompt({
                type: InteractionModalEnum.INPUT_REQUESTED,
                ...payload
            });

            const { eTag, ...restResult } = result ?? {};
            void reRunNode({
                nodeId: payload.node_id,
                runId: payload.run_id,
                taskId: payload.task_id,
                eTag: eTag as string,
                payload: result ? restResult : createConfirmRejectPayload("reject")
            })
        };

        const onHumanActionRequested = async (
            payload: SocketEventPayloadMap[SocketEvent.InputRequested]
        ) => {
            if (!latestRef.current.localExecution) return;

            setNodeExecution(payload.node_id, {
                status: "executing",
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "executing");

            const result = await latestRef.current.prompt({
                type: InteractionModalEnum.HUMAN_ACTION_REQUESTED,
                ...payload
            });

            const { eTag, ...restResult } = result ?? {};

            void reRunNode({
                nodeId: payload.node_id,
                runId: payload.run_id,
                taskId: payload.task_id,
                eTag: eTag as string,
                payload: result ? restResult : createConfirmRejectPayload("reject")
            })
        };

        const onDataEditRequested = async (payload: SocketEventPayloadMap[SocketEvent.InputRequested]) => {
            if (!latestRef.current.localExecution) return;

            setNodeExecution(payload.node_id, {
                status: "executing",
                timestamp: new Date().getTime()
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "executing");

            const result = await latestRef.current.prompt({
                type: InteractionModalEnum.DATA_EDIT_REQUESTED,
                ...payload
            });

            const { eTag, ...restResult } = result ?? {};
            void reRunNode({
                nodeId: payload.node_id,
                runId: payload.run_id,
                taskId: payload.task_id,
                eTag: eTag as string,
                payload: result ? restResult : createConfirmRejectPayload("reject")
            })
        };

        const onConditionEvaluated = (payload: SocketEventPayloadMap[SocketEvent.ConditionEvaluated]) => {
            if (!latestRef.current.localExecution) return;

            setNodeExecution(payload.node_id, {
                status: "completed",
                runId: payload.run_id,
                cacheKey: "",
                timestamp: new Date().getTime(),
                output: undefined,
                hasLargeOutput: false
            });

            editorRef.current?.setNodeStatus?.(payload.node_id, "completed");
        };

        const onEdgeTraversed = (payload: SocketEventPayloadMap[SocketEvent.EdgeTraversed]) => {
            if (!latestRef.current.localExecution) return;

            editorRef.current?.setConnectionStatusBySourcePort?.(payload.source_node_id, payload.source_port_id, "completed");
        };

        const onNodeApprovalFlowResult = async (payload: SocketEventPayloadMap[SocketEvent.NodeApprovalFlowResult]) => {
            try {
                editorRef.current?.setNodeStatus?.(payload.node_id, "completed");

                await latestRef.current.prompt({
                    type: InteractionModalEnum.APPROVAL_FLOW_VIEWER,
                    ...payload
                });

            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : undefined;
                toast.error(
                    t("notification.error"),
                    errorMessage ?? t("notification.approval_flow_failed_to_load")
                );
            }
        };

        const methodByEvent = {
            [SocketEvent.RunStatus]: (payload: SocketEventPayloadMap[SocketEvent.RunStatus]) => {
                if ('status' in payload) {
                    const status = payload.status;
                    if (status === "started") onRunStarted();
                    else if (status === "completed") onRunCompleted(payload);
                    else if (status === "failed") onRunFailed(payload);
                }
            },
            [SocketEvent.NodeDispatched]: (payload: SocketEventPayloadMap[SocketEvent.NodeDispatched]) => {
                void onTaskDispatched(payload);
            },
            [SocketEvent.NodeCompleted]: onTaskCompleted,
            [SocketEvent.NodeFailed]: onTaskFailed,
            [SocketEvent.NodeCancelled]: onTaskCancelled,
            [SocketEvent.ChildRunStarted]: onRunChildRunStarted,
            [SocketEvent.ChildRunCompleted]: onRunChildRunCompleted,
            [SocketEvent.ConditionEvaluated]: onConditionEvaluated,
            [SocketEvent.EdgeTraversed]: onEdgeTraversed,
            [SocketEvent.InputRequested]: (payload: SocketEventPayloadMap[SocketEvent.InputRequested]) => {
                if (payload?.pending_kind === "human_action") {
                    void onHumanActionRequested(payload);
                } else {
                    void onInputRequested(payload);
                }
            },
            [SocketEvent.DataEditRequested]: onDataEditRequested,
            [SocketEvent.NodeApprovalFlowResult]: onNodeApprovalFlowResult
        }

        const handleDataChunk = (socketMessage: unknown) => {
            const nestedData = normalizeSocketPayload(socketMessage);
            if (!nestedData) {
                return;
            }

            const event = getSocketEventName(nestedData);
            if (!event) {
                return;
            }


            const payload = getSocketEventPayload(nestedData);

            console.log({ event, payload: nestedData });

            const handler = methodByEvent[event as keyof typeof methodByEvent];
            if (handler) {
                void handler(payload);
            }
        }

        socket.on("data_chunk", handleDataChunk);

        return () => {
            socket.emit("leave", { key: `run:${testRunId}` });
            socket.off("connect", handleConnect);
            socket.off("reconnect", handleConnect);
            socket.off("reconnect_failed", handleReconnectFailed);

            socket.off("data_chunk", handleDataChunk);

            pushGatewaySocket.disconnect();
        };
    }, [testRunId]);

    useEffect(() => {
        return () => {
            editorRef.current?.resetAllNodeStatus?.();
            resetRunToast();
        }
    }, []);

    return {
        onRunNode,
        onRunWorkflow,
        stopWorkflow: handleStopWorkflow,

        isRunNodeLoading,
        loadingNodeId: isRunNodeLoading ? runNodeVariables?.nodeId : null,
        isPrepareWorkflowExecuting: isRunWorkflowLoading,
        isRunWorkflowExecuting: isWorkflowRunning
    };
};

export default useExecuteWorkflow;
