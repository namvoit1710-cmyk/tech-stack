import useNodeDetail from "@/features/workflows/hooks/use-node-detail";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useUIPanelStore } from "@/features/workflows/stores/ui-panel-stores";
import { useLanguage } from "@/hooks/use-language";
import type { BuilderRef } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import type { BaseNode } from "@ldc/workflow-editor";
import { LoadingSpin } from "@ldc/workflow-editor";
import { InfoIcon, PenIcon, PlayIcon } from "lucide-react";
import { useCallback, useMemo, useRef } from "react";
import NodesPopupContent from "./detail-content";
import NodeDetailProvider from "./node-detail-provider";

interface INodesDetailModalProps {
    headerText?: string;
    onClose?: () => void;
    isRunNodeLoading?: boolean
    loadingNodeId?: string | null
    onSelectNode: (node: BaseNode) => void
    onExecuteNode: (nodeId: string) => void
    removeConnection: ({ sourceId, targetId }: { sourceId?: string, targetId?: string }) => void
    updateNodeView: (nodeId: string) => void
    onParametersChanged?: () => void
}

const NodesDetailModal = ({
    onExecuteNode,
    isRunNodeLoading,
    loadingNodeId,
    onSelectNode,
    removeConnection,
    updateNodeView,
    onParametersChanged
}: INodesDetailModalProps) => {
    const { t } = useLanguage();

    const builderRef = useRef<BuilderRef>(null);
    // Populated by the parameter form; flushes its 200ms debounced commit so a
    // click on "Execute Step" tests the just-typed values, not the prior ones.
    const flushParamsRef = useRef<(() => void) | null>(null);

    const nodeDetailData = useNodeDetail();

    const isOpenNodesPopup = useUIPanelStore(s => s.isOpenNodesPopup);
    const setIsOpenNodesPopup = useUIPanelStore(s => s.setIsOpenNodesPopup);

    const isOpenNodeInfoEditorModal = useUIPanelStore(s => s.isOpenNodeInfoEditorModal);
    const setIsOpenNodeInfoEditorModal = useUIPanelStore(s => s.setIsOpenNodeInfoEditorModal);

    const selectedNode = useEditorStore(s => s.selectedNode);
    const setSelectedNode = useEditorStore(s => s.setSelectedNode);

    const currentNodeStatus = useEditorStore(
        useCallback(s => s.nodeExecutionMap[selectedNode?.id ?? ""]?.status ?? "idle", [selectedNode?.id])
    );
    const isExecuting = currentNodeStatus === "executing";
    const isCurrentNodeLoading = isRunNodeLoading && loadingNodeId === selectedNode?.id;

    const handleClose = () => {
        setIsOpenNodesPopup(false);
        setSelectedNode(null);
    };

    const contextValue = useMemo(() => ({
        onExecuteNode,
        onSelectNode,
        removeConnection,
        updateNodeView,
        onParametersChanged,
        closeNodeDetail: handleClose,

        isRunNodeLoading,
        loadingNodeId,
        builderRef,
        flushParamsRef,

        ...nodeDetailData
    }), [
        onExecuteNode,
        onSelectNode,
        removeConnection,
        updateNodeView,
        onParametersChanged,

        isRunNodeLoading,
        loadingNodeId,
        builderRef,
        flushParamsRef,

        handleClose,
        nodeDetailData
    ]);

    const handleExecuteClick = useCallback(() => {
        // Commit any pending (debounced) parameter edit before the test runs,
        // otherwise a quick click tests the previously-saved value.
        flushParamsRef.current?.();
        onExecuteNode?.(selectedNode?.id ?? "");
    }, [onExecuteNode, selectedNode?.id]);

    return (
        <Dialog
            open={isOpenNodesPopup}

            onOpenChange={(open) => {
                if (!open) {
                    handleClose();
                }
            }}
        >
            <DialogContent
                showCloseButton={false}
                onEscapeKeyDown={(e) => e.preventDefault()}
                onPointerDownOutside={(e) => e.preventDefault()}
                className="min-w-[95vw] h-[95vh] flex flex-col p-0 gap-0"
            >
                <DialogHeader>
                    <div className="flex items-center justify-between w-full h-12 border-b border-gray-200 gap-4 px-4">
                        <div className="flex-2 flex justify-between items-center group overflow-hidden">
                            <div className="flex items-center gap-2">
                                <DialogTitle className="scroll-m-20 text-lg font-semibold tracking-tight uppercase flex-2 gap-2">
                                    {!isOpenNodeInfoEditorModal && (selectedNode?.original?.title ?? selectedNode?.original?.name ?? t("nodes.nodes_popup_header"))}

                                </DialogTitle>

                                {(selectedNode?.original?.description || selectedNode?.original?.instruction) && (
                                    <TooltipProvider>
                                        <Tooltip defaultOpen={false}>
                                            <TooltipTrigger asChild>
                                                <InfoIcon className="size-4 text-primary" />
                                            </TooltipTrigger>

                                            <TooltipContent>
                                                <div className="max-w-xs flex flex-col gap-2">
                                                    {selectedNode?.original?.description && (
                                                        <div className="font-medium text-sm">
                                                            <span>{t("nodes.description")}</span>
                                                            <p className="text-sm text-gray-500">
                                                                {selectedNode.original.description}
                                                            </p>
                                                        </div>
                                                    )}
                                                    {selectedNode?.original?.instruction && (
                                                        <div className="font-medium text-sm">
                                                            <span>{t("nodes.instruction")}</span>
                                                            <p className="text-sm text-gray-500">
                                                                {selectedNode.original.instruction}
                                                            </p>
                                                        </div>
                                                    )}
                                                </div>
                                            </TooltipContent>
                                        </Tooltip>
                                    </TooltipProvider>
                                )}

                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="hover:bg-gray-200 hover:text-gray-700"
                                    onClick={() => {
                                        setIsOpenNodeInfoEditorModal(true);
                                    }}
                                >
                                    <PenIcon className="size-4 text-primary" />
                                </Button>

                            </div>

                            <Button
                                variant="default"
                                disabled={isCurrentNodeLoading || isExecuting}
                                onClick={handleExecuteClick}
                            >
                                <div className="flex items-center gap-2">
                                    <span className="size-4 inline-flex items-center justify-center text-white">
                                        {(isCurrentNodeLoading || isExecuting) ? <LoadingSpin /> : <PlayIcon className="size-4" />}
                                    </span>

                                    <span className="text-sm inline-block">
                                        {t("nodes.execute_step")}
                                    </span>
                                </div>
                            </Button>
                        </div>
                    </div>
                </DialogHeader>

                <section className="w-full flex-2 overflow-hidden p-4">
                    <NodeDetailProvider value={contextValue}>
                        <NodesPopupContent />
                    </NodeDetailProvider>
                </section>

                <section className="w-full border-t border-gray-200 flex items-center justify-end px-4 py-2 gap-4">
                    <Button
                        variant="outline"
                        onClick={handleClose}
                    >
                        {t("close")}
                    </Button>
                </section>
            </DialogContent>
        </Dialog>
    )
}

export default NodesDetailModal;