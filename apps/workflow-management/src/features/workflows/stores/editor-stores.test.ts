import type { BaseNode, IEditorConnectionValue, IEditorNodeValue } from "@ldc/workflow-editor"
import { beforeEach, describe, expect, it } from "@rstest/core"
import { act } from "@testing-library/react"
import type { NodeExecutionState } from "../types/execution"
import type { IWorkflow, IWorkflowSavePayload } from "../types/workflows"
import { useEditorStore } from "./editor-stores"

const makeWorkflow = (id: string): IWorkflow =>
  ({ id, name: `Workflow ${id}`, status: "DRAFT", nodes: [], edges: [] }) as unknown as IWorkflow

const makeNode = (id: string): BaseNode => ({ id }) as BaseNode

const makeEditorNode = (id: string): IEditorNodeValue => ({
  id,
  position: { x: 0, y: 0 },
  data: {
    id,
    name: id,
    title: id,
    node_type: "default",
    color: "#000",
    description: "",
    status: "idle",
    ports: { inputs: [], outputs: [] },
  },
})

const makeConnection = (id: string): IEditorConnectionValue =>
  ({ id, source: "node-1", target: "node-2", sourceOutput: "out", targetInput: "in" })

const makeExecution = (status: NodeExecutionState["status"]): NodeExecutionState => ({
  status,
  cacheKey: "cache-1",
  timestamp: 10,
})

const makeSavePayload = (id: string): IWorkflowSavePayload => ({
  id,
  name: `Workflow ${id}`,
  status: "DRAFT",
  nodes: [],
  edges: [],
})

describe("useEditorStore", () => {
  beforeEach(() => {
    act(() => useEditorStore.getState().resetEditorStore())
  })

  it("starts with empty editor state", () => {
    const state = useEditorStore.getState()

    expect(state.workflowInfo).toBeNull()
    expect(state.selectedVersionId).toBeNull()
    expect(state.baseChangeVersion).toBeNull()
    expect(state.baselineSavePayload).toBeNull()
    expect(state.isDirty).toBe(false)
    expect(state.selectedNode).toBeNull()
    expect(state.localExecution).toBeNull()
    expect(state.predecessorNodes).toEqual([])
    expect(state.outgoerNodes).toEqual([])
    expect(state.incomerNodes).toEqual([])
    expect(state.workflowData).toEqual({ nodes: [], connections: [] })
    expect(state.nodeExecutionMap).toEqual({})
  })

  it("setWorkflowInfo stores the workflow", () => {
    act(() => useEditorStore.getState().setWorkflowInfo(makeWorkflow("wf-1")))

    expect(useEditorStore.getState().workflowInfo?.id).toBe("wf-1")
  })

  it("stores and clears selected version state", () => {
    act(() => useEditorStore.getState().setSelectedVersionId("ver-1"))
    expect(useEditorStore.getState().selectedVersionId).toBe("ver-1")

    act(() => useEditorStore.getState().setSelectedVersionId(null))
    expect(useEditorStore.getState().selectedVersionId).toBeNull()
  })

  it("stores and clears base change version", () => {
    act(() => useEditorStore.getState().setBaseChangeVersion("v1.2.3"))
    expect(useEditorStore.getState().baseChangeVersion).toBe("v1.2.3")

    act(() => useEditorStore.getState().setBaseChangeVersion(null))
    expect(useEditorStore.getState().baseChangeVersion).toBeNull()
  })

  it("stores baseline save payload and dirty state", () => {
    act(() => {
      useEditorStore.getState().setBaselineSavePayload(makeSavePayload("wf-1"))
      useEditorStore.getState().setIsDirty(true)
    })

    const state = useEditorStore.getState()
    expect(state.baselineSavePayload?.id).toBe("wf-1")
    expect(state.isDirty).toBe(true)
  })

  it("setSelectedNode stores and clears the selected node", () => {
    act(() => useEditorStore.getState().setSelectedNode(makeNode("node-1")))
    expect(useEditorStore.getState().selectedNode?.id).toBe("node-1")

    act(() => useEditorStore.getState().setSelectedNode(null))
    expect(useEditorStore.getState().selectedNode).toBeNull()
  })

  it("setPredecessorNodes, setOutgoerNodes, setIncomerNodes store node lists", () => {
    act(() => {
      useEditorStore.getState().setPredecessorNodes([makeNode("pre-1")])
      useEditorStore.getState().setOutgoerNodes([makeNode("out-1")])
      useEditorStore.getState().setIncomerNodes([makeNode("in-1")])
    })

    const state = useEditorStore.getState()
    expect(state.predecessorNodes[0]?.id).toBe("pre-1")
    expect(state.outgoerNodes[0]?.id).toBe("out-1")
    expect(state.incomerNodes[0]?.id).toBe("in-1")
  })

  it("setWorkflowConnections merges connections into workflowData without replacing nodes", () => {
    act(() => {
      useEditorStore.getState().setWorkflowData({ nodes: [makeEditorNode("node-1")], connections: [] })
      useEditorStore.getState().setWorkflowConnections([makeConnection("conn-1")])
    })

    const { workflowData } = useEditorStore.getState()
    expect(workflowData.connections).toHaveLength(1)
    expect(workflowData.connections[0]?.id).toBe("conn-1")
    expect(workflowData.nodes).toHaveLength(1)
  })

  it("setNodeExecution stores execution state per node", () => {
    act(() => useEditorStore.getState().setNodeExecution("node-1", makeExecution("executing")))

    expect(useEditorStore.getState().nodeExecutionMap["node-1"]).toMatchObject({
      status: "executing",
      cacheKey: "cache-1",
      timestamp: 10,
    })
  })

  it("resetNodeExecution removes a single node entry", () => {
    act(() => {
      useEditorStore.getState().setNodeExecution("node-1", makeExecution("completed"))
      useEditorStore.getState().setNodeExecution("node-2", makeExecution("failed"))
    })

    act(() => useEditorStore.getState().resetNodeExecution("node-1"))

    expect(useEditorStore.getState().nodeExecutionMap["node-1"]).toBeUndefined()
    expect(useEditorStore.getState().nodeExecutionMap["node-2"]).toBeDefined()
  })

  it("resetAllNodeExecutions clears the entire execution map", () => {
    act(() => {
      useEditorStore.getState().setNodeExecution("node-1", makeExecution("executing"))
      useEditorStore.getState().setNodeExecution("node-2", makeExecution("failed"))
    })

    act(() => useEditorStore.getState().resetAllNodeExecutions())

    expect(useEditorStore.getState().nodeExecutionMap).toEqual({})
  })

  it("setLocalExecution stores and clears local execution context", () => {
    act(() => useEditorStore.getState().setLocalExecution({ type: "node", nodeId: "node-1" }))
    expect(useEditorStore.getState().localExecution).toEqual({ type: "node", nodeId: "node-1" })

    act(() => useEditorStore.getState().setLocalExecution(null))
    expect(useEditorStore.getState().localExecution).toBeNull()
  })

  it("resetEditorStore restores all fields to initial state", () => {
    act(() => {
      useEditorStore.getState().setWorkflowInfo(makeWorkflow("wf-2"))
      useEditorStore.getState().setSelectedVersionId("ver-2")
      useEditorStore.getState().setBaseChangeVersion("v1.0.0")
      useEditorStore.getState().setBaselineSavePayload(makeSavePayload("wf-2"))
      useEditorStore.getState().setIsDirty(true)
      useEditorStore.getState().setSelectedNode(makeNode("node-1"))
      useEditorStore.getState().setNodeExecution("node-1", makeExecution("completed"))
      useEditorStore.getState().setLocalExecution({ type: "workflow", runId: "run-1" })
    })

    act(() => useEditorStore.getState().resetEditorStore())

    const state = useEditorStore.getState()
    expect(state.workflowInfo).toBeNull()
    expect(state.selectedVersionId).toBeNull()
    expect(state.baseChangeVersion).toBeNull()
    expect(state.baselineSavePayload).toBeNull()
    expect(state.isDirty).toBe(false)
    expect(state.selectedNode).toBeNull()
    expect(state.localExecution).toBeNull()
    expect(state.nodeExecutionMap).toEqual({})
    expect(state.workflowData).toEqual({ nodes: [], connections: [] })
  })
})
