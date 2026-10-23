import { describe, expect, it, rs } from "@rstest/core";
import serviceRequest from "./service-request";

const makeService = () => ({
    fetch: rs.fn().mockResolvedValue("GET_RESULT"),
    post: rs.fn().mockResolvedValue("POST_RESULT"),
    put: rs.fn().mockResolvedValue("PUT_RESULT"),
    patch: rs.fn().mockResolvedValue("PATCH_RESULT"),
    delete: rs.fn().mockResolvedValue("DELETE_RESULT"),
});

describe("serviceRequest (utils)", () => {
    // happy — GET routes to service.fetch(url) only
    it("routes GET to service.fetch with the url", async () => {
        const svc = makeService();
        await expect(serviceRequest(svc, "GET", "/x")).resolves.toBe("GET_RESULT");
        expect(svc.fetch).toHaveBeenCalledWith("/x");
    });

    // happy — POST passes url, body, headers
    it("routes POST to service.post with url, body, headers", async () => {
        const svc = makeService();
        const body = { a: 1 };
        const headers = { "X-H": "1" };
        await expect(serviceRequest(svc, "POST", "/x", body, headers)).resolves.toBe("POST_RESULT");
        expect(svc.post).toHaveBeenCalledWith("/x", body, headers);
    });

    // happy — PUT / PATCH
    it("routes PUT and PATCH with url, body, headers", async () => {
        const svc = makeService();
        await serviceRequest(svc, "PUT", "/p", { b: 2 }, { h: "1" });
        await serviceRequest(svc, "PATCH", "/q", { c: 3 });
        expect(svc.put).toHaveBeenCalledWith("/p", { b: 2 }, { h: "1" });
        expect(svc.patch).toHaveBeenCalledWith("/q", { c: 3 }, undefined);
    });

    // happy — DELETE passes url + headers (no body)
    it("routes DELETE to service.delete with url and headers", async () => {
        const svc = makeService();
        await serviceRequest(svc, "DELETE", "/z", undefined, { h: "1" });
        expect(svc.delete).toHaveBeenCalledWith("/z", { h: "1" });
    });

    // failure path — unsupported method throws
    it("throws on an unsupported method", async () => {
        const svc = makeService();
        await expect(serviceRequest(svc, "HEAD", "/x")).rejects.toThrow("Unsupported method: HEAD");
    });
});
