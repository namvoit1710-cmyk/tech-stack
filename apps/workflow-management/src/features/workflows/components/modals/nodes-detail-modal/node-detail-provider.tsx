import type { IUseNodeDetailReturn } from "@/features/workflows/types/node-detail";
import type { BuilderRef } from "@ldc/autoform";
import type { BaseNode } from "@ldc/workflow-editor";
import type { RefObject } from "react";
import { createContext, useContext } from "react";

interface INodeDetailContext extends IUseNodeDetailReturn {
    isRunNodeLoading?: boolean
    loadingNodeId?: string | null
    builderRef?: RefObject<BuilderRef | null>
    // Set by the parameter form to lodash-debounce.flush() its pending commit.
    // "Execute Step" invokes it first so a fast click (< the 200ms param debounce)
    // still commits the latest edit before the node test dispatches.
    flushParamsRef?: RefObject<(() => void) | null>
    closeNodeDetail: () => void
    onSelectNode: (node: BaseNode) => void
    onExecuteNode: (nodeId: string) => void
    removeConnection: ({ sourceId, targetId }: { sourceId?: string, targetId?: string }) => void
    updateNodeView: (nodeId: string) => void
    onParametersChanged?: () => void
}

interface INodeDetailProviderProps {
    children: React.ReactNode
    value: INodeDetailContext
}

const Context = createContext<INodeDetailContext>({
    selectedNode: null,
    currentNodeStatus: "idle",
    showOutputSchema: false,
    outputScopedVariablesTransformed: [],
    isLoadingOutputSchema: false,
    scopedVariables: [],
    exprSuggestions: [],
    variableQuery: "",
    setVariableQuery: () => null,
    refetchVariableInput: () => null,
    nodeDetail: undefined,
    isNodeDetailLoading: false,

    onSelectNode: () => null,
    onExecuteNode: () => null,
    removeConnection: () => null,
    closeNodeDetail: () => null,
    updateNodeView: () => null,
    onParametersChanged: undefined,
})

const NodeDetailProvider = ({ children, value }: INodeDetailProviderProps) => {
    return (
        <Context.Provider value={value}>
            {children}
        </Context.Provider>
    )
}

export const useNodeDetailContext = () => {
    return useContext(Context)
}

export default NodeDetailProvider