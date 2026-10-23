import { describe, it, expect, rs, beforeEach } from "@rstest/core"

rs.mock("@ldc/api-sdk", () => ({
  APISdk: rs.fn().mockReturnValue({})
}))
rs.mock("@/env", () => ({
  env: {
    PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE: "http://fake-ai",
    PUBLIC_URL_FILE_SERVICE: "http://fake-file",
    PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL: "http://fake-validation",
    PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL: "http://fake-profile",
    PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL: "http://fake-supervisor",
    PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL: "http://fake-designer"
  }
}))

describe("api.ts", () => {
  beforeEach(() => {
    rs.resetModules()
    rs.clearAllMocks()
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { href: "" }
    })
  })

  it("constructs all API instances with correct baseURL", async () => {
    const { APISdk } = await import("@ldc/api-sdk")
    await import("../api")
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-ai/api/v1" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-ai/api/v2" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-file/api/v1" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-validation" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-profile" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-supervisor" }))
    expect(APISdk).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "http://fake-designer" }))
  })

  it("sets onSessionExpired handler to redirect to /login", async () => {
    const { APISdk } = await import("@ldc/api-sdk")
    let sessionExpiredHandler: (() => void) | undefined
    const MockedAPISdk = rs.mocked(APISdk)
    MockedAPISdk.mockImplementation((opts: { onSessionExpired?: () => void }) => {
      if (opts.onSessionExpired) sessionExpiredHandler = opts.onSessionExpired
      return {} as InstanceType<typeof APISdk>
    })
    await import("../api")
    if (!sessionExpiredHandler) throw new Error("sessionExpiredHandler was never captured")
    sessionExpiredHandler()
    expect(window.location.href).toBe("/login")
  })

  it("does not set onSessionExpired on agent APIs", async () => {
    const { APISdk } = await import("@ldc/api-sdk")
    const calls: { onSessionExpired?: () => void }[] = []
    const MockedAPISdk = rs.mocked(APISdk)
    MockedAPISdk.mockImplementation((opts: { onSessionExpired?: () => void }) => {
      calls.push(opts)
      return {} as InstanceType<typeof APISdk>
    })
    await import("../api")
    // 4 agent API clients (workflowValidationRuleAgent, workflowProfileManager,
    // workflowAgent, workflowDesignerAgent) have no session redirect
    const withoutHandler = calls.filter((opts) => !opts.onSessionExpired)
    expect(withoutHandler).toHaveLength(4)
  })

  it("exports all API instances and default export", async () => {
    const mod = await import("../api")
    expect(mod.api).toBeDefined()
    expect(mod.apiV2).toBeDefined()
    expect(mod.fileApi).toBeDefined()
    expect(mod.workflowAgentApi).toBeDefined()
    expect(mod.workflowDesignerAgentApi).toBeDefined()
    expect(mod.workflowProfileManagerApi).toBeDefined()
    expect(mod.workflowValidationRuleAgentApi).toBeDefined()
    expect(mod.default).toBe(mod.api)
  })
})
