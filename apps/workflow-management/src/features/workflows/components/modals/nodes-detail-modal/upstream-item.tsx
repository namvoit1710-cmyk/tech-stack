import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useLanguage } from "@/hooks/use-language";

import JsonView from "@/components/json-view/json-view";
import type { IScopedVariable } from "@/features/workflows/types/node-detail";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@ldc/ui/components/accordion";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import type { BaseNode } from "@ldc/workflow-editor";
import { DynamicNodeIcon, LoadingSpin } from "@ldc/workflow-editor";
import { isEmpty } from "lodash-es";
import { PlayIcon } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useNodeDetailContext } from "./node-detail-provider";

interface IUpstreamNodesProps {
    nodes: BaseNode[]
    variableNodes: IScopedVariable[]
    reloadInputSchema: () => void
}

const UpstreamNodes = ({ nodes, variableNodes, reloadInputSchema }: IUpstreamNodesProps) => {
    const { t } = useLanguage()
    const { onExecuteNode, isRunNodeLoading } = useNodeDetailContext()

    const nodeExecutionMap = useEditorStore(s => s.nodeExecutionMap)

    const variableByNodeId = useMemo(() => {
        return variableNodes.reduce(
            (acc: Record<string, IScopedVariable>, node: IScopedVariable) => {
                if (!node.scopeId) return acc

                acc[node.scopeId] = node
                return acc
            }, {})
    }, [variableNodes])

    return (
        <TooltipProvider>
            <Accordion type="single" defaultValue={nodes[0]?.id} className="w-full">
                {nodes.map(node => {
                    const isExecuting = nodeExecutionMap[node.id]?.status === "executing"

                    return (
                        <AccordionItem key={node.id} value={node.id} className="border-0">
                            <AccordionTrigger
                                className="justify-start [&>svg]:hidden hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md flex items-center gap-1"
                            >
                                <Tooltip>
                                    <TooltipTrigger>
                                        <span className="flex items-center justify-center cursor-pointer px-1" onClick={(e) => {
                                            onExecuteNode(node.id)
                                            e.stopPropagation()
                                        }}>
                                            {(isExecuting) ?
                                                <LoadingSpin /> :
                                                <PlayIcon className="size-4" strokeWidth={1.5} />
                                            }
                                        </span>

                                    </TooltipTrigger>

                                    <TooltipContent>
                                        {t("nodes.execute_previous_node")}
                                    </TooltipContent>
                                </Tooltip>

                                <span className="flex items-center gap-2">
                                    <DynamicNodeIcon name={node?.original?.icon ?? ""} color={node?.original?.color} className="size-5" />
                                    <span>{node?.original?.title}</span>
                                </span>

                            </AccordionTrigger>

                            <AccordionContent className="px-3 py-2">
                                <UpstreamNodeItem node={node} reloadInputSchema={reloadInputSchema} variableNode={variableByNodeId[node.id]} />
                            </AccordionContent>
                        </AccordionItem>
                    )
                })}
            </Accordion>
        </TooltipProvider>
    )
}

interface IUpstreamNodeItemProps {
    node: BaseNode
    variableNode: IScopedVariable | undefined
    reloadInputSchema: () => void
}

const UpstreamNodeItem = ({ node, variableNode, reloadInputSchema }: IUpstreamNodeItemProps) => {

    const { t } = useLanguage()


    const variableNodeObject = useMemo(() => Object.entries(variableNode?.paths ?? {}).reduce((acc, [key, value]) => {
        acc[key] = value.sample
        return acc
    }, {} as Record<string, any>), [variableNode])

    const hasVariableNode = useMemo(() => !!variableNode && !isEmpty(variableNodeObject), [variableNodeObject])

    const nodeExecutionMap = useEditorStore(s => s.nodeExecutionMap)

    const upstreamNodeStatus = useMemo(() => nodeExecutionMap[node.id]?.status ?? "idle", [nodeExecutionMap, node.id])
    const isIdleStatus = useMemo(() => upstreamNodeStatus === "idle", [upstreamNodeStatus])
    const showInputSchema = useMemo(() => upstreamNodeStatus === "completed", [upstreamNodeStatus])

    useEffect(() => {
        if (showInputSchema) {
            reloadInputSchema()
        }
    }, [showInputSchema])

    return (
        <>
            {(isIdleStatus || !hasVariableNode) && (
                <div className="space-x-1 w-full flex justify-center">
                    <span className="text-sm text-center">{t("nodes.to_view_input_data")}</span>
                </div>
            )}

            {hasVariableNode && !isIdleStatus && (
                <JsonView
                    value={variableNodeObject}
                    prefix={variableNode?.expressionPrefix ? `{{${variableNode.expressionPrefix}.#path}}` : undefined}
                    enableClipboard={false}
                    draggableKeys={true}
                    raw={variableNode?.paths}
                    replacePathKey="displayPath"
                />
            )}
        </>
    )
}

export { UpstreamNodeItem, UpstreamNodes };

