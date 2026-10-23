import { useEditorStore } from "@/features/workflows/stores/editor-stores"
import type { IScopedVariable } from "@/features/workflows/types/node-detail"
import { useLanguage } from "@/hooks/use-language"
import { cn } from "@ldc/ui"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@ldc/ui/components/accordion"
import { Input } from "@ldc/ui/components/input"
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback"
import { SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import ArtifactItem from "./artifact-item"
import InputSchema from "./input-schema"
import { useNodeDetailContext } from "./node-detail-provider"
import { UpstreamNodes } from "./upstream-item"

// Generic accordion section for a single context scope (variables / run / workflow). Renders the
// scope's paths via the shared InputSchema (draggable / clipboard JsonView keyed by displayPath).
// Skipped when the scope has no paths, so empty scopes don't clutter the panel.
const ScopeSection = ({ scope }: { scope: IScopedVariable }) => {
    if (!Object.keys(scope.paths).length) return null

    return (
        <Accordion type="multiple" className="w-full mt-4">
            <AccordionItem key={scope.scopeId} value={scope.scopeId} className="border-0">
                <AccordionTrigger className="justify-start px-1 w-full hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                    <span className="flex items-center gap-2 flex-2">
                        <span className="font-medium">{scope.label}</span>
                    </span>
                </AccordionTrigger>
                <AccordionContent className="px-3 py-2">
                    <InputSchema scopePath={scope.paths} />
                </AccordionContent>
            </AccordionItem>
        </Accordion>
    )
}

const NodeDetailInput = () => {
    const { t } = useLanguage()

    const {
        isRunNodeLoading,
        loadingNodeId,
        scopedVariables,
        isLoadingInput,
        refetchVariableInput,
        variableQuery,
        setVariableQuery,
    } = useNodeDetailContext()
    const selectedNode = useEditorStore(s => s.selectedNode)
    const selectedNodeId = selectedNode?.id
    const predecessorNodes = useEditorStore(s => s.predecessorNodes)

    const [searchInput, setSearchInput] = useState(variableQuery ?? "")
    const debouncedSetQuery = useDebounceCallback(setVariableQuery, 400)

    const isUpstreamLoading = isRunNodeLoading && loadingNodeId !== selectedNodeId

    const nodeVariablesInputs = useMemo(() => {
        return scopedVariables.filter(item => item.scopeType === "node")
    }, [scopedVariables])

    const inputVariablesInputs = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "input")
    }, [scopedVariables])

    const variablesScope = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "variables")
    }, [scopedVariables])

    const runScope = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "run")
    }, [scopedVariables])

    const workflowScope = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "workflow")
    }, [scopedVariables])

    // SA-2365: {{$env.KEY}} — the selected environment's config variables (incl.
    // credential refs). Rendered like the other context scopes so a designer can
    // drag env values into node fields.
    const envScope = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "env")
    }, [scopedVariables])

    const artifactVariablesInputs = useMemo(() => {
        return scopedVariables.find(item => item.scopeType === "artifacts")
    }, [scopedVariables])

    const hasUpstreamNodes = useMemo(() => {
        return predecessorNodes?.length > 0
    }, [predecessorNodes])

    // Context scopes exist independently of the DAG (a source node still has input/variables/run/
    // workflow/artifacts). Used to keep those sections visible when there are no upstream nodes.
    const hasOtherScopes = useMemo(
        () => !!inputVariablesInputs || !!variablesScope || !!runScope || !!workflowScope || !!envScope || !!artifactVariablesInputs,
        [inputVariablesInputs, variablesScope, runScope, workflowScope, envScope, artifactVariablesInputs]
    )

    return (
        <div className="flex flex-col gap-4 w-full h-full overflow-hidden!">
            <div className="flex items-center justify-between px-1.5">
                <h5 className="uppercase font-semibold">{t("nodes.nodes_popup_input")}</h5>
            </div>

            <div className="px-1.5">
                <div className="relative">
                    <Input
                        className="pl-9 h-8 text-sm"
                        placeholder={t("node_variables.search_placeholder")}
                        value={searchInput}
                        onChange={(e) => {
                            setSearchInput(e.target.value)
                            debouncedSetQuery(e.target.value)
                        }}
                    />
                    <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
                </div>
            </div>

            <div className="relative h-full flex-2 w-full overflow-hidden">
                <div
                    className={cn(
                        "h-full w-full overflow-auto pr-2",
                        (!hasUpstreamNodes && !hasOtherScopes) && "flex-2 overflow-hidden"
                    )}
                >
                    {!hasUpstreamNodes && !hasOtherScopes && (
                        <>
                            <div className="flex-2 flex items-center justify-center h-full">
                                <span className="text-sm text-gray-500">{t("nodes.nodes_popup_no_upstream_nodes")}</span>
                            </div>
                        </>
                    )}

                    {hasUpstreamNodes && (
                        <>
                            <UpstreamNodes
                                nodes={predecessorNodes}
                                variableNodes={nodeVariablesInputs}
                                reloadInputSchema={refetchVariableInput}
                            />
                        </>
                    )}

                    {!!inputVariablesInputs && (
                        <Accordion type="multiple" className="w-full mt-4">
                            <AccordionItem key={inputVariablesInputs.scopeId} value={inputVariablesInputs.scopeId} className="border-0">
                                <AccordionTrigger className="justify-start px-1 w-full hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                                    <span className="flex items-center gap-2 flex-2">
                                        <span className="font-medium">{inputVariablesInputs.label}</span>
                                    </span>
                                </AccordionTrigger>
                                <AccordionContent className="px-3 py-2">
                                    {!!Object.keys(inputVariablesInputs.paths).length && (
                                        <InputSchema scopePath={inputVariablesInputs.paths} />
                                    )}

                                    {!Object.keys(inputVariablesInputs.paths).length && (
                                        <span className="text-sm text-gray-500 text-center block">
                                            {t("nodes.nodes_popup_no_input_data")}
                                        </span>
                                    )}
                                </AccordionContent>
                            </AccordionItem>

                        </Accordion>
                    )}

                    {!!variablesScope && <ScopeSection scope={variablesScope} />}

                    {!!runScope && <ScopeSection scope={runScope} />}

                    {!!workflowScope && <ScopeSection scope={workflowScope} />}

                    {!!envScope && <ScopeSection scope={envScope} />}

                    {!!artifactVariablesInputs && (
                        <Accordion type="multiple" className="w-full mt-4">
                            <AccordionItem key={artifactVariablesInputs.scopeId} value={artifactVariablesInputs.scopeId} className="border-0">
                                <AccordionTrigger className="justify-start px-1 w-full hover:no-underline cursor-pointer hover:bg-gray-100 py-2 rounded-md">
                                    <span className="flex items-center gap-2 flex-2">
                                        <span className="font-medium">{artifactVariablesInputs.label}</span>
                                    </span>
                                </AccordionTrigger>
                                <AccordionContent className="px-3 py-2">
                                    <ArtifactItem paths={artifactVariablesInputs.paths} enableDragKey />
                                </AccordionContent>
                            </AccordionItem>

                        </Accordion>
                    )}
                </div>

                {(isUpstreamLoading || isLoadingInput) && (
                    <div className="absolute inset-0 h-full bg-white/50 backdrop-blur-xs z-30 flex items-center justify-center">
                        <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                )}
            </div>
        </div>
    )
}

export default NodeDetailInput
