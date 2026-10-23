import type { BaseNode, IEditorConnectionValue, IEditorValue } from "@ldc/workflow-editor";
import { create } from "zustand";
import type { NodeExecutionState } from "../types/execution";
import type { IWorkflow, IWorkflowSavePayload } from "../types/workflows";

interface EditorState {
    workflowInfo: IWorkflow | null;
    selectedVersionId: string | null;
    // SA-2365: environment selected for the next run; bound as the $env source.
    selectedEnvironmentId: string | null;
    baseChangeVersion: string | null;
    unsavedDraftBaseVersion: string | null;
    baselineSavePayload: IWorkflowSavePayload | null;
    isDirty: boolean;
    unsavedDraft: IEditorValue | null;
    selectedNode: BaseNode | null;
    predecessorNodes: BaseNode[];
    outgoerNodes: BaseNode[];
    incomerNodes: BaseNode[];
    workflowData: IEditorValue;
    localExecution: {
        type: "node" | "workflow" | null;
        nodeId?: string;
        runId?: string;
    } | null,
    nodeExecutionMap: Record<string, NodeExecutionState>;
}

interface EditorActions {
    setWorkflowInfo: (data: IWorkflow) => void;
    setSelectedVersionId: (versionId: string | null) => void;
    setSelectedEnvironmentId: (id: string | null) => void;
    setBaseChangeVersion: (version: string | null) => void;
    setUnsavedDraftBaseVersion: (version: string | null) => void;
    setBaselineSavePayload: (payload: IWorkflowSavePayload | null) => void;
    setIsDirty: (isDirty: boolean) => void;
    setUnsavedDraft: (draft: IEditorValue | null) => void;
    setSelectedNode: (node: BaseNode | null) => void;
    setPredecessorNodes: (nodes: BaseNode[]) => void;
    setOutgoerNodes: (nodes: BaseNode[]) => void;
    setIncomerNodes: (nodes: BaseNode[]) => void;
    setWorkflowData: (data: IEditorValue) => void;
    setWorkflowConnections: (connections: IEditorConnectionValue[]) => void;

    setNodeExecution: (nodeId: string, state: NodeExecutionState) => void;
    resetNodeExecution: (nodeId: string) => void;
    resetAllNodeExecutions: () => void;

    setLocalExecution: (execution: EditorState["localExecution"]) => void;

    resetEditorStore: () => void;
}

type EditorStore = EditorState & EditorActions;

const initialState: EditorState = {
    workflowInfo: null,
    selectedVersionId: null,
    selectedEnvironmentId: null,
    baseChangeVersion: null,
    unsavedDraftBaseVersion: null,
    baselineSavePayload: null,
    isDirty: false,
    unsavedDraft: null,
    selectedNode: null,
    localExecution: null,
    predecessorNodes: [],
    outgoerNodes: [],
    incomerNodes: [],
    workflowData: { nodes: [], connections: [] },
    nodeExecutionMap: {},
};

export const useEditorStore = create<EditorStore>((set, get) => ({
    ...initialState,

    setWorkflowInfo: (data) => set({ workflowInfo: data }),
    setSelectedVersionId: (versionId) => set({ selectedVersionId: versionId }),
    setSelectedEnvironmentId: (id) => set({ selectedEnvironmentId: id }),
    setBaseChangeVersion: (version) => set({ baseChangeVersion: version }),
    setUnsavedDraftBaseVersion: (version) => set({ unsavedDraftBaseVersion: version }),
    setBaselineSavePayload: (payload) => set({ baselineSavePayload: payload }),
    setIsDirty: (isDirty) => set({ isDirty }),
    setUnsavedDraft: (draft) => set({ unsavedDraft: draft }),
    setSelectedNode: (node) => set({ selectedNode: node }),
    setPredecessorNodes: (nodes) => set({ predecessorNodes: nodes }),
    setOutgoerNodes: (nodes) => set({ outgoerNodes: nodes }),
    setIncomerNodes: (nodes) => set({ incomerNodes: nodes }),
    setWorkflowData: (data) => set({ workflowData: data }),
    setWorkflowConnections: (connections) => set({ workflowData: { ...get().workflowData, connections } }),

    setNodeExecution: (nodeId, state) => set({
        nodeExecutionMap: { ...get().nodeExecutionMap, [nodeId]: state },
    }),
    resetNodeExecution: (nodeId) => {
        const { [nodeId]: _, ...rest } = get().nodeExecutionMap;
        set({ nodeExecutionMap: rest });
    },
    resetAllNodeExecutions: () => set({ nodeExecutionMap: {} }),

    setLocalExecution: (execution) => set({ localExecution: execution }),

    resetEditorStore: () => set(initialState),
}));