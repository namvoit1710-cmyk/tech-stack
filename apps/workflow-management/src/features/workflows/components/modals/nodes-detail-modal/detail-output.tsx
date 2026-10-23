
import { useEditorStore } from "@/features/workflows/stores/editor-stores"
import { useLanguage } from "@/hooks/use-language"
import { cn } from "@ldc/ui"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@ldc/ui/components/accordion"
import { useMemo } from "react"
import ArtifactItem from "./artifact-item"
import InputSchema from "./input-schema"
import { useNodeDetailContext } from "./node-detail-provider"

const getIterationLabel = (iterationIndex: number | null, position: number): string => {
    if (iterationIndex !== null) {
        return `Iteration ${iterationIndex + 1}`
    }

    return `Iteration ${position + 1}`
}

const getIterationKey = (taskId: string, iterationIndex: number | null, position: number): string => {
    return `${taskId}-${iterationIndex ?? position}-${position}`
}

const NodeDetailOutput = () => {
    const { t } = useLanguage()
    const selectedNode = useEditorStore(s => s.selectedNode)
    const currentNodeStatus = useEditorStore(s => s.nodeExecutionMap[selectedNode?.id ?? ""]?.status ?? "idle")
    const isExecuting = currentNodeStatus === "executing"

    const { isLoadingOutputSchema, outputScopedVariablesTransformed } = useNodeDetailContext()

    const iterationSections = useMemo(() => {
        return outputScopedVariablesTransformed.map((iteration, position) => {
            const nodeVariablesOutputs = iteration.scopes.filter(
                item => item.scopeType !== "input" && item.scopeType !== "artifacts"
            )
            const artifactVariablesOutputs = iteration.scopes.filter(item => item.scopeType === "artifacts")

            return {
                ...iteration,
                artifactVariablesOutputs,
                nodeVariablesOutputs,
                label: getIterationLabel(iteration.iterationIndex, position),
            }
        })
    }, [outputScopedVariablesTransformed])

    const hasAnyOutputs = useMemo(() => {
        return iterationSections.some(
            iteration => iteration.nodeVariablesOutputs.length || iteration.artifactVariablesOutputs.length
        )
    }, [iterationSections])

    return (
        <div className="flex flex-col gap-4 w-full h-full overflow-hidden">
            <div className="flex items-center justify-between px-4">
                <h5 className="uppercase font-semibold">{t("nodes.nodes_popup_output")}</h5>
            </div>

            <div className="relative h-full flex-2 w-full overflow-hidden">
                <div
                    className={cn(
                        "h-full w-full overflow-auto px-2",
                        !hasAnyOutputs && "overflow-hidden"
                    )}
                >
                    {!hasAnyOutputs && (
                        <div className="flex-2 flex flex-col items-center justify-center h-full">
                            <span className="text-sm text-gray-500 text-center">{t("nodes.nodes_popup_no_output_data")}</span>
                            <span className="text-sm text-gray-500 text-center">{t("nodes.let_run_the_node_to_view_output_data")}</span>
                        </div>
                    )}

                    <Accordion type="multiple" className="w-full">
                        {iterationSections.map((iteration, iterationPosition) => {
                            const iterationKey = getIterationKey(
                                iteration.taskId,
                                iteration.iterationIndex,
                                iterationPosition
                            )

                            return (
                                <div key={iterationKey} className="mb-3 border-b last:border-b-0 last:mb-0">
                                    {iteration.nodeVariablesOutputs.length < 2 && (
                                        <div className="py-2">
                                            <div className="flex items-center justify-between gap-3 rounded-md bg-gray-50 px-2 py-2">
                                                <span className="text-sm font-semibold text-gray-700">{iteration.label}</span>
                                                <span className="text-xs uppercase tracking-wide text-gray-500">{iteration.status}</span>
                                            </div>
                                        </div>
                                    )}

                                    {!iteration.nodeVariablesOutputs.length && !iteration.artifactVariablesOutputs.length && (
                                        <div className="px-4 py-2 text-sm text-gray-500">
                                            {t("nodes.nodes_popup_no_output_data")}
                                        </div>
                                    )}

                                    {iteration.nodeVariablesOutputs.map(nodeScope => {
                                        const itemValue = `${iterationKey}-${nodeScope.scopeId}`
                                        const scopePaths = nodeScope.paths ?? {}

                                        return (
                                            <AccordionItem key={itemValue} value={itemValue} className="border-0">
                                                <AccordionTrigger className="justify-start px-1 w-full hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                                                    <span className="flex items-center gap-2 flex-2">
                                                        <span className="font-medium">{nodeScope.label}</span>
                                                    </span>
                                                </AccordionTrigger>
                                                <AccordionContent className="px-3 py-2">
                                                    {!!Object.keys(scopePaths).length && (
                                                        <InputSchema scopePath={scopePaths} draggable={false} />
                                                    )}

                                                    {!Object.keys(scopePaths).length && (
                                                        <span className="text-sm text-gray-500 text-center block">
                                                            {t("nodes.nodes_popup_no_output_data")}
                                                        </span>
                                                    )}
                                                </AccordionContent>
                                            </AccordionItem>
                                        )
                                    })}

                                    {iteration.artifactVariablesOutputs.map(artifactScope => {
                                        const itemValue = `${iterationKey}-${artifactScope.scopeId}`

                                        return (
                                            <AccordionItem key={itemValue} value={itemValue} className="border-0">
                                                <AccordionTrigger className="justify-start px-1 w-full hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                                                    <span className="flex items-center gap-2 flex-2">
                                                        <span className="font-medium">{artifactScope.label}</span>
                                                    </span>
                                                </AccordionTrigger>
                                                <AccordionContent className="px-3 py-2">
                                                    <ArtifactItem paths={artifactScope.paths} enableDragKey={false} />
                                                </AccordionContent>
                                            </AccordionItem>
                                        )
                                    })}
                                </div>
                            )
                        })}
                    </Accordion>
                </div>

                {(isLoadingOutputSchema || isExecuting) && (
                    <div className="absolute inset-0 h-full bg-white/50 backdrop-blur-xs z-30 flex items-center justify-center">
                        <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                )}
            </div>
        </div>
    )
}

export default NodeDetailOutput