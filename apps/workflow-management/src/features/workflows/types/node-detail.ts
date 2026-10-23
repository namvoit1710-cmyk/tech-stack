import type { BaseNode } from "@ldc/workflow-editor"
import type { NodeExecutionStatus } from "./execution"
import type { INodeCatalogDetail } from "./node-pallete"
export type IOutputSchemaHierarchical = unknown

export interface IScopedVariablePath {
    sample: unknown
    displayPath: string
    kind: string
    outputType: string
}

export interface IScopedVariable {
    label: string
    scopeType: string
    scopeId: string
    expressionPrefix: string
    paths: Record<string, IScopedVariablePath>
    artifacts: unknown[]
    hasFileData: boolean
    fileId: string | null
    columns: string[]
    previewRows: unknown[]
}

// Flattened, ready-to-insert suggestion for the inline expression typeahead.
export interface IExprSuggestion {
    value: string   // inserted text, e.g. "{{$nodeA.output.field}}"
    label: string   // display name (path tail)
    description?: string
    group: string   // scope label, used as the dropdown group header
}

export interface INodeOutputIterationTransformed {
    iterationIndex: number | null
    taskId: string
    status: string
    startedAt: string | null
    completedAt: string | null
    outputPort: string
    takenEdgePorts: Record<string, unknown>
    error: string | null
    errorCode: string
    scopes: IScopedVariable[]
}

export interface IUseNodeDetailReturn {
    // Node identity
    selectedNode: BaseNode | null
    currentNodeId?: string
    runId?: string

    currentNodeStatus: NodeExecutionStatus
    showOutputSchema: boolean

    outputScopedVariablesTransformed: INodeOutputIterationTransformed[]
    isLoadingOutputSchema: boolean

    scopedVariables: IScopedVariable[]
    exprSuggestions: IExprSuggestion[]
    variableQuery: string
    setVariableQuery: (query: string) => void
    isLoadingInput?: boolean
    refetchVariableInput: () => void

    nodeDetail: INodeCatalogDetail | undefined
    isNodeDetailLoading: boolean
}

export interface INodeComprehensiveQueryParams {
    nodeId: string
    runId: string
    offset?: number
    limit?: number
    include_samples?: boolean
    max_depth?: number
    include_params?: boolean
    include_artifacts?: boolean
    include_output_data?: boolean
    include_schema?: boolean
    schema_path?: string
}

export interface ITreeNode {
    key: string
    children?: ITreeNode[]
    sample?: unknown
}

export type INodeComprehensiveData = Record<string, unknown>
