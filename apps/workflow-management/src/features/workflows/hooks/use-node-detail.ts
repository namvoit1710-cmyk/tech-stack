import { useMemo, useState } from "react"
import { useEditorStore } from "../stores/editor-stores"
import { useUIPanelStore } from "../stores/ui-panel-stores"
import type { NodeExecutionStatus } from "../types/execution"
import type {
    IExprSuggestion,
    INodeOutputIterationTransformed,
    IScopedVariable,
    IUseNodeDetailReturn,
} from "../types/node-detail"
import {
    normalizeDesignTimeMatchesToScopes,
    scopedVariablesToExprSuggestions,
    transformNodeOutputScopesToScopedVariables,
    transformVariableSuggestionScopesToScopedVariables,
} from "../utils/node-detail-utils"
import { useQueryNodeCatalogDetail } from "./apis/node-pallete"
import { useGetDesignTimeVariableSuggestions, useGetNodeOutput, useGetNodeVariableSuggestions } from "./apis/workflows"

// Narrow scoped variables (and their paths) to those matching the search term.
const filterScopedVariablesByQuery = (vars: IScopedVariable[], query: string): IScopedVariable[] => {
    const term = query.trim().toLowerCase()
    if (!term) return vars
    return vars
        .map(scope => ({
            ...scope,
            paths: Object.fromEntries(
                Object.entries(scope.paths).filter(([label, pathItem]) =>
                    label.toLowerCase().includes(term) || pathItem.displayPath.toLowerCase().includes(term)
                )
            ),
        }))
        .filter(scope => Object.keys(scope.paths).length > 0)
}

const useNodeDetail = (): IUseNodeDetailReturn => {
    const selectedNode = useEditorStore(s => s.selectedNode)
    const nodeExecutionMap = useEditorStore(s => s.nodeExecutionMap)
    const isOpenNodesPopup = useUIPanelStore(s => s.isOpenNodesPopup);
    const runId = useEditorStore(s => s.workflowInfo?.test_run_id)
    const workflowId = useEditorStore(s => s.workflowInfo?.id)
    const selectedEnvironmentId = useEditorStore(s => s.selectedEnvironmentId)
    const hasRun = !!runId

    const [variableQuery, setVariableQuery] = useState("")

    const currentNodeId = selectedNode?.id

    // The node-catalog is keyed by a namespaced palette id: worker nodes are
    // "worker:<type>" while built-ins are already "builtin:<type>". A node stores the
    // worker_type; prefix "worker:" ONLY when it has no namespace, else use as-is —
    // otherwise "builtin:trigger" would become "worker:builtin:trigger" and 404.
    const _wt = selectedNode?.original.worker_type ?? ""
    const _catalogRef = _wt ? (_wt.includes(":") ? _wt : `worker:${_wt}`) : ""
    const { data: nodeCatalogDetail, isLoading: isNodeDetailLoading } = useQueryNodeCatalogDetail(
        { ref: _catalogRef },
        {
            enabled: !!_wt,
        }
    )

    const currentNodeStatus: NodeExecutionStatus =
        nodeExecutionMap[currentNodeId ?? ""]?.status ?? "idle"

    const showOutputSchema = currentNodeStatus === "completed" || currentNodeStatus === "failed"

    const {
        data: outputSchema,
        isLoading: isLoadingOutputSchema,
    } = useGetNodeOutput(
        {
            nodeId: currentNodeId ?? "",
            runId: runId ?? "",
        },
        {
            enabled: showOutputSchema && !!currentNodeId && !!runId,
            staleTime: 0
        }
    )

    // Transform each output iteration and normalize nested scopes to the shared scoped-variable shape.
    const outputScopedVariablesTransformed = useMemo<INodeOutputIterationTransformed[]>(() => {
        if (!outputSchema?.data?.iterations?.length) return []

        return outputSchema.data.iterations.map(iteration => {
            const scopes = transformNodeOutputScopesToScopedVariables(iteration.scopes ?? [])

            return {
                scopes,
                status: iteration.status,
                taskId: iteration.task_id,
                startedAt: iteration.started_at,
                outputPort: iteration.output_port,
                completedAt: iteration.completed_at,
                iterationIndex: iteration.iteration_index,
                takenEdgePorts: iteration.taken_edge_ports,
                error: iteration.error,
                errorCode: iteration.error_code,
            }
        })
    }, [outputSchema])

    // Run-scoped suggestions (with runtime samples) when a test run exists.
    const { data: nodeVariableResponse, isLoading: isLoadingVariable, refetch } = useGetNodeVariableSuggestions({
        runId: runId ?? "",
        nodeId: currentNodeId ?? "",
        environmentId: selectedEnvironmentId ?? "",
    }, {
        enabled: !!isOpenNodesPopup && hasRun && !!currentNodeId,
    })

    // Design-time fallback (no run): {{$...}} hints while authoring on the canvas. `q` filters server-side.
    const { data: designTimeResponse, isLoading: isLoadingDesignTime } = useGetDesignTimeVariableSuggestions({
        workflowId: workflowId ?? "",
        nodeId: currentNodeId ?? "",
        q: variableQuery,
        environmentId: selectedEnvironmentId ?? "",
    }, {
        enabled: !!isOpenNodesPopup && !hasRun && !!workflowId && !!currentNodeId,
    })

    // Unify both sources into the shared scoped-variable shape, then apply the search term.
    // All scope types (run/workflow/variables/input/node/artifacts) flow through to both the
    // inline {{ typeahead and the browse panel — only the search term narrows the set.
    const scopedVariables = useMemo<IScopedVariable[]>(() => {
        let scopes: IScopedVariable[]
        if (hasRun) {
            scopes = transformVariableSuggestionScopesToScopedVariables(nodeVariableResponse?.data?.scopes ?? [])
        } else {
            const dt = designTimeResponse?.data
            const normalized = dt?.matches?.length
                ? normalizeDesignTimeMatchesToScopes(dt.matches, dt.scopes ?? [])
                : []
            scopes = transformVariableSuggestionScopesToScopedVariables(normalized)
        }
        return filterScopedVariablesByQuery(scopes, variableQuery)
    }, [hasRun, nodeVariableResponse, designTimeResponse, variableQuery])

    const exprSuggestions = useMemo<IExprSuggestion[]>(
        () => scopedVariablesToExprSuggestions(scopedVariables),
        [scopedVariables]
    )

    return {
        runId,
        selectedNode,
        currentNodeId,
        currentNodeStatus,
        showOutputSchema,

        outputScopedVariablesTransformed: outputScopedVariablesTransformed,

        nodeDetail: nodeCatalogDetail?.data,
        isNodeDetailLoading,

        refetchVariableInput: () => {
            void refetch()
        },
        scopedVariables,
        exprSuggestions,
        variableQuery,
        setVariableQuery,
        isLoadingInput: isLoadingVariable || isLoadingDesignTime,
        isLoadingOutputSchema,
    }
}

export default useNodeDetail
