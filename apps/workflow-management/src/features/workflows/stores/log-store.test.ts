import { beforeEach, describe, expect, it } from "@rstest/core"
import { act } from "@testing-library/react"
import type { IWorkflowLogs } from "../types/workflow-log"
import { useWorkflowLogStore } from "./log-store"

const makeLog = (index: number): IWorkflowLogs => ({
  id: `log-${index}`,
  workflowId: "wf-1",
  actionName: "NODE_ADDED",
  timestamp: index,
})

describe("useWorkflowLogStore", () => {
  beforeEach(() => {
    localStorage.clear()
    act(() => useWorkflowLogStore.getState().clearLogs())
  })

  it("starts with empty logs", () => {
    expect(useWorkflowLogStore.getState().logs).toEqual([])
  })

  it("prepends new log to the front", () => {
    act(() => {
      useWorkflowLogStore.getState().addLog(makeLog(1))
      useWorkflowLogStore.getState().addLog(makeLog(2))
    })

    const logs = useWorkflowLogStore.getState().logs
    expect(logs[0]?.id).toBe("log-2")
    expect(logs[1]?.id).toBe("log-1")
  })

  it("caps log list at 500 items", () => {
    act(() => {
      for (let i = 0; i < 501; i++) {
        useWorkflowLogStore.getState().addLog(makeLog(i))
      }
    })

    const logs = useWorkflowLogStore.getState().logs
    expect(logs).toHaveLength(500)
    expect(logs[0]?.id).toBe("log-500")
    expect(logs[499]?.id).toBe("log-1")
  })

  it("clearLogs empties the log list", () => {
    act(() => useWorkflowLogStore.getState().addLog(makeLog(1)))
    expect(useWorkflowLogStore.getState().logs).toHaveLength(1)

    act(() => useWorkflowLogStore.getState().clearLogs())
    expect(useWorkflowLogStore.getState().logs).toEqual([])
  })

  it("persists logs to localStorage under the workflow-logs key", () => {
    act(() => useWorkflowLogStore.getState().addLog(makeLog(1)))

    const stored = localStorage.getItem("workflow-logs")
    expect(stored).not.toBeNull()
    const parsed = JSON.parse(stored!)
    expect(parsed.state.logs[0].id).toBe("log-1")
  })
})
