import { describe, expect, it, rs } from "@rstest/core";
import { makeGatewayInvoke } from "./gateway-invoke";

const okEnvelope = { status: "ok", result: { status_code: 200, headers: {}, body: { status: "DONE", id: 7 } } };
const servicesWithPost = (post: (...a: unknown[]) => unknown) => ({ workflow: { post } } as never);

describe("makeGatewayInvoke — happy path", () => {
    it("POSTs { params: { app, function, args } } and returns result.body", async () => {
        const post = rs.fn().mockResolvedValue(okEnvelope);
        const out = await makeGatewayInvoke(servicesWithPost(post)).invoke("workflow-api", "submit", { a: 1 });
        expect(out).toEqual({ status: "DONE", id: 7 });
        expect(post).toHaveBeenCalledWith(
            "workers/gateway/functions/invoke_app_function/invoke",
            { params: { app: "workflow-api", function: "submit", args: { a: 1 } } },
        );
    });
    it("defaults args to {} when omitted", async () => {
        const post = rs.fn().mockResolvedValue(okEnvelope);
        await makeGatewayInvoke(servicesWithPost(post)).invoke("app", "fn");
        expect(post).toHaveBeenCalledWith(expect.any(String), { params: { app: "app", function: "fn", args: {} } });
    });
    it("unwraps the control-plane { status, data: { result } } envelope (api-sdk returns response.data)", async () => {
        const post = rs.fn().mockResolvedValue({
            status: "ok",
            data: { status: "success", result: { status_code: 200, headers: {}, body: { valid: "correct" } } },
        });
        const out = await makeGatewayInvoke(servicesWithPost(post)).invoke("workflow-validate", "execute", { message: "m" });
        expect(out).toEqual({ valid: "correct" });
    });
});

describe("makeGatewayInvoke — errors", () => {
    const servicesWithPost = (post: (...a: unknown[]) => unknown) => ({ workflow: { post } } as never);

    it("throws with status_code/body/error when result.status_code >= 400", async () => {
        const post = rs.fn().mockResolvedValue({ result: { status_code: 422, body: { detail: "bad" }, error: "Unprocessable" } });
        await expect(makeGatewayInvoke(servicesWithPost(post)).invoke("app", "fn")).rejects.toMatchObject({
            status_code: 422, body: { detail: "bad" }, error: "Unprocessable",
        });
    });
    it("propagates transport errors (rejected post)", async () => {
        const post = rs.fn().mockRejectedValue(new Error("Network down"));
        await expect(makeGatewayInvoke(servicesWithPost(post)).invoke("app", "fn")).rejects.toThrow("Network down");
    });
    it("throws a clear error when the workflow service is not configured", async () => {
        await expect(makeGatewayInvoke(undefined).invoke("app", "fn")).rejects.toThrow(/service "workflow" is not configured/);
    });
});
