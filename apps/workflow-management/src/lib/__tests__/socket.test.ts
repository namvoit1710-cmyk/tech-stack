import { describe, it, expect, rs } from "@rstest/core"

const socketClientMock = rs.fn().mockReturnValue({})
const socketModulePromise = import("../socket")

rs.mock("@ldc/api-sdk/socket", () => ({
  SocketClient: socketClientMock
}))
rs.mock("@/env", () => ({
  env: {
    PUBLIC_URL_PUSH_GATEWAY: "http://fake-push"
  }
}))

describe("socket.ts", () => {
  it("constructs pushGatewaySocket with correct options", async () => {
    await socketModulePromise
    expect(socketClientMock).toHaveBeenCalledWith(expect.objectContaining({
      baseUrl: "http://fake-push",
      timeout: 20000,
      reconnection: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000
    }))
  })

  it("constructs exactly one socket instance", async () => {
    await socketModulePromise
    expect(socketClientMock).toHaveBeenCalledTimes(1)
  })

  it("exports pushGatewaySocket", async () => {
    const socketModule = await socketModulePromise
    expect(socketModule.pushGatewaySocket).toBeDefined()
  })
})
