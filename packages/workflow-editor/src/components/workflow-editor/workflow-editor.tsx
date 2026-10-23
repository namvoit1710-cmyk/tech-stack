import { useTranslation } from "@ldc/i18n";
import { forwardRef, memo, useCallback, useImperativeHandle, useRef, useState } from "react";
import { DEFAULT_SIZE_NODE } from "../../constants/node";
import { useEditorClipboard } from "../../hooks/use-editor-clipboard";
import { useEditorDnd } from "../../hooks/use-editor-dnd";
import { useEditorSetup } from "../../hooks/use-editor-setup";
import { useEditorSync } from "../../hooks/use-editor-sync";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../i18n";
import { generateUUID } from "../../utils/generate-uuid";
import { getNodeSize } from "../rete-editor/config/node-config";
import { BaseNode } from "../rete-editor/nodes/base-node";
import type { EditorDirection, IEditorNode, IEditorValue, NodeExecutionStatus } from "../rete-editor/types";
import { Alert } from "./alert-dialog/messagebox-provider";
import EditorContextMenu from "./editor-context-menu/editor-context-menu";
import NodeContextMenu, { NodeContextMenuAction } from "./node-context-menu/node-context-menu";
import BottomToolbar from "./toolbar/bottom-toolbar";
import type { IExtension } from "./toolbar/side-toolbar";
import SideToolBar from "./toolbar/side-toolbar";

interface IWorkflowEditorProps {
    value: IEditorValue;
    readOnly?: boolean;
    sidebarProps?: {
        extensions?: IExtension[];
    };
    direction?: EditorDirection;
    isShowToolbar?: boolean;

    onLoadedData?: () => void;
    onAddNode?: () => void;
    onChange?: (value: IEditorValue) => void;
    onNodeSelected?: (node: BaseNode) => void;
    onExecuteNode?: (nodeId: string) => void;
    onOpenNodePopup?: (node: BaseNode) => void;
    onNodeAdded?: () => void;
    onEditNode?: (node: BaseNode) => void;
    onConnectionAdded?: () => void;
}

export interface WorkflowEditorHandle {
    getNodeById?: (nodeId: string) => BaseNode | undefined;
    getNodes?: () => BaseNode[];

    addNode?: (nodePallete: IEditorNode) => void;
    updateNodeView?: (nodeId: string) => void;
    removeAllNodes?: () => Promise<void>;

    getPredecessorNodes?: (nodeId: string) => BaseNode[];
    getOutGoerNodes?: (nodeId: string) => BaseNode[];
    getIncomerNodes?: (nodeId: string) => BaseNode[];

    removeConnectionBySource?: (sourceNodeId: string) => void;
    removeConnectionByTarget?: (targetNodeId: string) => void;

    addInputSocket?: (nodeId: string, key: string, label: string) => Promise<void>;
    addOutputSocket?: (nodeId: string, key: string, label: string) => Promise<void>;
    removeInputSocket?: (nodeId: string, key: string) => Promise<void>;
    removeOutputSocket?: (nodeId: string, key: string) => Promise<void>;
    updateInputSocketLabel?: (nodeId: string, key: string, label: string) => Promise<void>;
    updateOutputSocketLabel?: (nodeId: string, key: string, label: string) => Promise<void>;

    getZoomLevel?: () => number;
    zoomToFit?: () => void;
    layout?: () => void;
    zoomByLevel?: (zoomLevel: number) => Promise<void>;
    centerOnNode?: (nodeId: string) => Promise<void>;
    isNodeInViewport?: (nodeId: string) => boolean;

    resetAllNodeStatus?: () => void;
    setNodeStatus?: (nodeId: string, status: NodeExecutionStatus) => void;
    updateNodeConnectionStatus?: (nodeId: string, status: NodeExecutionStatus) => void;
    setConnectionStatusBySourcePort?: (sourceNodeId: string, sourcePortId: string, status: NodeExecutionStatus) => void;
    setConnectionStatusByTargetPort?: (targetNodeId: string, targetPortId: string, status: NodeExecutionStatus) => void;

    serializeAndEmitChange?: () => void;
    getSerializedValue?: () => IEditorValue | undefined;
}

const WorkflowEditor = forwardRef<WorkflowEditorHandle, IWorkflowEditorProps>(
    (
        {
            value,
            readOnly = false,
            sidebarProps = {},
            isShowToolbar = true,
            direction = "horizontal",

            onChange,
            onAddNode,
            onEditNode,
            onNodeAdded,
            onLoadedData,
            onExecuteNode,
            onNodeSelected,
            onOpenNodePopup,
            onConnectionAdded,
        },
        ref
    ) => {
        const [nodeContext, setNodeContext] = useState<{ nodeRef: HTMLDivElement, nodeId: string } | null>(null);
        const containerRef = useRef<HTMLDivElement>(null);

        const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

        const [alertInstance, setAlertInstance] = useState<{ title: string, message: string, onConfirm: () => void } | null>(null);

        const openNodeContext = (ref: HTMLDivElement, nodeId: string) => {
            setNodeContext({ nodeRef: ref, nodeId });
        };

        const { ref: editorDomRef, editorInstance: editorInstanceOrNull, isLoadingRef } = useEditorSetup(value, {
            openNodeContext,
            onOpenNodePopup,
            readOnly,
            onLoadedData,
            direction: direction
        });

        const editorInstance = editorInstanceOrNull ?? undefined;

        useEditorSync(editorInstance, isLoadingRef, { onChange, onNodeSelected, onNodeAdded, onConnectionAdded });

        const { dragPreview, previewRef, dndHandlers } = useEditorDnd(editorInstance, readOnly);

        const { clipboardHandlers, isHandlerLoading } = useEditorClipboard({ editorInstance, isLoadingRef, readOnly, onChange });

        const handleContextMenuAction = useCallback(async (action: NodeContextMenuAction, nodeId: string) => {
            switch (action) {
                case NodeContextMenuAction.Delete: {
                    setAlertInstance({
                        title: t("confirmation.confirm_deletion"),
                        message: t("confirmation.delete_node"),
                        onConfirm: async () => {
                            await editorInstance?.removeNode(nodeId);
                            setAlertInstance(null);
                        }
                    });

                    break;
                }
                case NodeContextMenuAction.Copy:
                    await editorInstance?.copyNode(nodeId);
                    break;
                case NodeContextMenuAction.Open: {
                    const node = editorInstance?.getNodeById(nodeId);
                    if (node) {
                        onOpenNodePopup?.(node);
                    }
                    break;
                }
                case NodeContextMenuAction.Replace: {
                    const node = editorInstance?.getNodeById(nodeId);
                    if (node) {
                        onEditNode?.(node);
                    }
                    break;
                }
                case NodeContextMenuAction.Execute:
                    onExecuteNode?.(nodeId);
                    break;
                default:
                    break;
            }

            setNodeContext(null);
        }, [editorInstance]);

        const addNode = useCallback(async (editorNode: IEditorNode) => {
            if (!editorInstance) return;

            const node = new BaseNode(generateUUID(), editorNode.name, editorNode.ports, getNodeSize(editorNode.name), editorNode);

            await editorInstance.addNode(node);

            const nodes = await editorInstance.getNodes();
            const previousNode = nodes.length > 1 ? nodes.at(-2) : null;

            let newX = 0;
            let newY = 0;

            if (previousNode) {
                const prevPosition = editorInstance.getNodePosition(previousNode.id);
                if (prevPosition) {
                    newX = prevPosition.x + previousNode.width + 150;
                    newY = prevPosition.y;
                }
            } else {
                const transform = editorInstance.getTransform();
                const containerRect = containerRef.current?.getBoundingClientRect();
                if (containerRect) {
                    newX = (containerRect.width / 2 - transform.x) / transform.k - node.width / 2;
                    newY = (containerRect.height / 2 - transform.y) / transform.k - node.height / 2;
                }
            }

            await editorInstance.translateNode(node.id, { x: newX, y: newY });
            await editorInstance.zoomByLevel(1);
            await editorInstance.centerOnNode(node.id);
        }, [editorInstance]);

        const handleAddNode = useCallback(() => {
            if (onAddNode) {
                onAddNode?.()
                return;
            }
        }, [onAddNode])

        const handleEmitChange = useCallback(() => {
            if (!onChange) return;
            const nodes = editorInstance?.serializeNodes();
            if (!nodes) return;
            onChange(nodes);
        }, [onChange, editorInstance])

        useImperativeHandle(
            ref,
            () => {
                const commonMethods = {
                    getNodes() {
                        return editorInstance?.getNodes() ?? [];
                    },

                    getNodeById(nodeId: string) {
                        return editorInstance?.getNodeById(nodeId);
                    },

                    async setNodeStatus(nodeId: string, status: NodeExecutionStatus) {
                        editorInstance?.setNodeStatus(nodeId, status);
                    },

                    setConnectionStatusBySourcePort(sourceNodeId: string, sourcePortId: string, status: NodeExecutionStatus) {
                        editorInstance?.setConnectionStatusBySourcePort(sourceNodeId, sourcePortId, status);
                    },

                    setConnectionStatusByTargetPort(targetNodeId: string, targetPortId: string, status: NodeExecutionStatus) {
                        editorInstance?.setConnectionStatusByTargetPort(targetNodeId, targetPortId, status);
                    },

                    getPredecessorNodes(nodeId: string) {
                        return editorInstance?.getPredecessorNodes(nodeId) ?? [];
                    },

                    getOutGoerNodes(nodeId: string) {
                        return editorInstance?.getOutGoerNodes(nodeId) ?? [];
                    },

                    getIncomerNodes(nodeId: string) {
                        return editorInstance?.getIncomerNodes(nodeId) ?? [];
                    },

                    getZoomLevel() {
                        return editorInstance?.getZoomLevel() ?? 1;
                    },

                    zoomToFit() {
                        return editorInstance?.zoomToFit();
                    },

                    layout() {
                        return editorInstance?.layout();
                    },

                    zoomByLevel(zoomLevel: number) {
                        return editorInstance?.zoomByLevel(zoomLevel) ?? Promise.resolve();
                    },

                    isNodeInViewport(nodeId: string) {
                        return editorInstance?.isNodeInViewport(nodeId) ?? false;
                    },

                    centerOnNode(nodeId: string) {
                        return editorInstance?.centerOnNode(nodeId) ?? Promise.resolve();
                    },

                    resetAllNodeStatus() {
                        editorInstance?.resetAllNodeStatus();
                    },
                };

                const readOnlyMethods: WorkflowEditorHandle = commonMethods;

                const editableMethods: WorkflowEditorHandle = {
                    ...commonMethods,

                    removeAllNodes() {
                        return editorInstance?.removeAllNodes() ?? Promise.resolve();
                    },

                    removeConnectionBySource(sourceNodeId: string) {
                        editorInstance?.removeConnectionBySource(sourceNodeId);
                    },

                    removeConnectionByTarget(targetNodeId: string) {
                        editorInstance?.removeConnectionByTarget(targetNodeId);
                    },

                    updateNodeConnectionStatus(nodeId: string, status: NodeExecutionStatus) {
                        editorInstance?.updateNodeConnectionStatus(nodeId, status);
                    },

                    addInputSocket: (nodeId, key, label) => editorInstance?.addInputSocket(nodeId, key, label) ?? Promise.resolve(),
                    addOutputSocket: (nodeId, key, label) => editorInstance?.addOutputSocket(nodeId, key, label) ?? Promise.resolve(),
                    removeInputSocket: (nodeId, key) => editorInstance?.removeInputSocket(nodeId, key) ?? Promise.resolve(),
                    removeOutputSocket: (nodeId, key) => editorInstance?.removeOutputSocket(nodeId, key) ?? Promise.resolve(),
                    updateInputSocketLabel: (nodeId, key, label) => editorInstance?.updateInputSocketLabel(nodeId, key, label) ?? Promise.resolve(),
                    updateOutputSocketLabel: (nodeId, key, label) => editorInstance?.updateOutputSocketLabel(nodeId, key, label) ?? Promise.resolve(),

                    addNode,
                    updateNodeView: (nodeId) => editorInstance?.updateNodeView(nodeId) ?? Promise.resolve(),
                    serializeAndEmitChange: handleEmitChange,
                    getSerializedValue: () => editorInstance?.serializeNodes(),
                };

                return (readOnly || !editorInstance) ? readOnlyMethods : editableMethods;
            },
            [editorInstance, readOnly, addNode, handleEmitChange]
        );

        return (
            <>
                <div className="flex items-stretch w-full h-full overflow-hidden">
                    <div
                        ref={containerRef}
                        className="relative h-full w-full overflow-hidden flex-2 shadow-r-lg"
                        {...dndHandlers}
                    >
                        {isShowToolbar && !readOnly && (
                            <SideToolBar
                                {...sidebarProps}
                                clipboardHandlers={clipboardHandlers}
                                isHandlerLoading={isHandlerLoading}
                                onAddNode={handleAddNode}
                            />
                        )}

                        <EditorContextMenu
                            readOnly={readOnly}
                            handleAddNode={handleAddNode}
                            clipboardHandlers={clipboardHandlers}
                        >
                            <div
                                ref={editorDomRef}
                                className="w-full h-full relative overflow-hidden"
                                tabIndex={0}
                                {...clipboardHandlers}
                            >
                                {dragPreview && (
                                    <div
                                        ref={previewRef}
                                        className="absolute pointer-events-none z-50 -translate-x-1/2 -translate-y-1/2"
                                    >
                                        <div
                                            className="horizontal-nodes opacity-50 p-1 rounded-lg"
                                            style={{
                                                width: DEFAULT_SIZE_NODE.WIDTH,
                                                height: DEFAULT_SIZE_NODE.HEIGHT,
                                            }}
                                        >
                                            <div className="horizontal-nodes__content bg-card/30 backdrop-blur-sm border border-border border-dashed w-full h-full rounded-lg flex items-center justify-center">
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </EditorContextMenu>

                        <BottomToolbar editorInstance={editorInstance} readOnly={readOnly} />
                    </div>
                </div>

                <NodeContextMenu
                    readOnly={readOnly}
                    open={!!nodeContext}
                    onOpenChange={() => setNodeContext(null)}
                    anchorEl={nodeContext?.nodeRef}
                    onAction={(action) => {
                        handleContextMenuAction(action, nodeContext!.nodeId)
                    }}
                />

                <Alert
                    {...alertInstance}
                    onClose={() => setAlertInstance(null)}
                    open={!!alertInstance}
                />
            </>
        )
    }
);

WorkflowEditor.displayName = "WorkflowEditor";

function getValueKey(value: IEditorValue) {
    const nodeIds = (value?.nodes ?? []).map((n: any) => n.id).sort().join(",");
    const connIds = (value?.connections ?? []).map((c: any) => c.id).sort().join(",");
    return `${nodeIds}|${connIds}`;
}

export default memo(WorkflowEditor, (prevProps, nextProps) => {
    return getValueKey(prevProps.value) === getValueKey(nextProps.value);
});