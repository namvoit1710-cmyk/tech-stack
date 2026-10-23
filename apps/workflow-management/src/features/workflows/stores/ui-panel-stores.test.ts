import { act } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "@rstest/core"
import { useUIPanelStore } from "./ui-panel-stores"

describe("useUIPanelStore", () => {
  beforeEach(() => {
    act(() => useUIPanelStore.getState().resetUIPanelStore())
  })

  it("starts closed", () => {
    const state = useUIPanelStore.getState()

    expect(state.isOpenNodesPopup).toBe(false)
    expect(state.isOpenChatPanel).toBe(false)
    expect(state.isOpenNodeListPanel).toBe(false)
    expect(state.isOpenParameterSetting).toBe(false)
    expect(state.isOpenRunConfigModal).toBe(false)
    expect(state.isOpenNodeInfoEditorModal).toBe(false)
    expect(state.isLogSidebarOpen).toBe(false)
    expect(state.selectedRunId).toBeNull()
  })

  it("set actions update individual flags", () => {
    act(() => {
      useUIPanelStore.getState().setIsOpenNodesPopup(true)
      useUIPanelStore.getState().setIsOpenParameterSetting(true)
      useUIPanelStore.getState().setIsOpenNodeListPanel(true)
      useUIPanelStore.getState().setIsOpenNodeInfoEditorModal(true)
    })

    const state = useUIPanelStore.getState()
    expect(state.isOpenNodesPopup).toBe(true)
    expect(state.isOpenParameterSetting).toBe(true)
    expect(state.isOpenNodeListPanel).toBe(true)
    expect(state.isOpenNodeInfoEditorModal).toBe(true)
  })

  it("toggle actions flip boolean flags", () => {
    act(() => {
      useUIPanelStore.getState().setIsOpenNodesPopup(true)
      useUIPanelStore.getState().setIsOpenNodeListPanel(true)
    })

    act(() => {
      useUIPanelStore.getState().toggleNodesPopup()
      useUIPanelStore.getState().toggleChatPanel()
      useUIPanelStore.getState().toggleNodeListPanel()
    })

    const state = useUIPanelStore.getState()
    expect(state.isOpenNodesPopup).toBe(false)
    expect(state.isOpenChatPanel).toBe(true)
    expect(state.isOpenNodeListPanel).toBe(false)
  })

  it("openRunConfigModal and closeRunConfigModal control modal state", () => {
    act(() => useUIPanelStore.getState().openRunConfigModal())
    expect(useUIPanelStore.getState().isOpenRunConfigModal).toBe(true)

    act(() => useUIPanelStore.getState().closeRunConfigModal())
    expect(useUIPanelStore.getState().isOpenRunConfigModal).toBe(false)
  })

  it("closeNodesPopup sets isOpenNodesPopup to false", () => {
    act(() => useUIPanelStore.getState().setIsOpenNodesPopup(true))
    expect(useUIPanelStore.getState().isOpenNodesPopup).toBe(true)

    act(() => useUIPanelStore.getState().closeNodesPopup())
    expect(useUIPanelStore.getState().isOpenNodesPopup).toBe(false)
  })

  it("resetUIPanelStore restores all flags to false", () => {
    act(() => {
      useUIPanelStore.getState().setIsOpenNodesPopup(true)
      useUIPanelStore.getState().setIsOpenParameterSetting(true)
      useUIPanelStore.getState().openRunConfigModal()
      useUIPanelStore.getState().setIsOpenNodeInfoEditorModal(true)
    })

    act(() => useUIPanelStore.getState().resetUIPanelStore())

    const state = useUIPanelStore.getState()
    expect(state.isOpenNodesPopup).toBe(false)
    expect(state.isOpenChatPanel).toBe(false)
    expect(state.isOpenNodeListPanel).toBe(false)
    expect(state.isOpenParameterSetting).toBe(false)
    expect(state.isOpenRunConfigModal).toBe(false)
    expect(state.isOpenNodeInfoEditorModal).toBe(false)
    expect(state.isLogSidebarOpen).toBe(false)
    expect(state.selectedRunId).toBeNull()
  })

  it("setIsLogSidebarOpen and setSelectedRunId control log sidebar state", () => {
    act(() => {
      useUIPanelStore.getState().setIsLogSidebarOpen(true)
      useUIPanelStore.getState().setSelectedRunId("run-123")
    })

    expect(useUIPanelStore.getState().isLogSidebarOpen).toBe(true)
    expect(useUIPanelStore.getState().selectedRunId).toBe("run-123")

    act(() => {
      useUIPanelStore.getState().setIsLogSidebarOpen(false)
      useUIPanelStore.getState().setSelectedRunId(null)
    })

    expect(useUIPanelStore.getState().isLogSidebarOpen).toBe(false)
    expect(useUIPanelStore.getState().selectedRunId).toBeNull()
  })
})
