import { describe, expect, it, rs } from "@rstest/core";
import { serviceRequest } from "./service-request";
import type { ServiceHandler } from "../../../hooks/use-builder-services";

const makeService = () =>
    ({
        fetch: rs.fn().mockResolvedValue("FETCH"),
        post: rs.fn().mockResolvedValue("POST"),
        put: rs.fn().mockResolvedValue("PUT"),
        patch: rs.fn().mockResolvedValue("PATCH"),
        delete: rs.fn().mockResolvedValue("DELETE"),
    }) as unknown as ServiceHandler;

describe("serviceRequest (action-engine)", () => {
    // happy — GET calls fetch(url, body, headers)
    it("routes GET to fetch with url, body and headers", async () => {
        const svc = makeService();
        const body = { q: 1 };
        const headers = { h: "1" };
        await expect(serviceRequest(svc, "GET", "/g", body, headers)).resolves.toBe("FETCH");
        expect(svc.fetch).toHaveBeenCalledWith("/g", body, headers);
    });

    // happy — DELETE calls delete(url, headers)
    it("routes DELETE to delete with url and headers", async () => {
        const svc = makeService();
        await serviceRequest(svc, "DELETE", "/d", undefined, { h: "1" });
        expect(svc.delete).toHaveBeenCalledWith("/d", { h: "1" });
    });

    // happy — method is lowercased and dispatched dynamically (POST/PUT/PATCH)
    it("dispatches POST/PUT/PATCH via lowercased method name", async () => {
        const svc = makeService();
        await expect(serviceRequest(svc, "POST", "/p", { a: 1 }, { h: "1" })).resolves.toBe("POST");
        expect((svc as any).post).toHaveBeenCalledWith("/p", { a: 1 }, { h: "1" });

        await serviceRequest(svc, "PUT", "/u", { b: 2 });
        expect((svc as any).put).toHaveBeenCalledWith("/u", { b: 2 }, undefined);

        await serviceRequest(svc, "PATCH", "/c", { c: 3 });
        expect((svc as any).patch).toHaveBeenCalledWith("/c", { c: 3 }, undefined);
    });

    // edge — method is case-insensitive (lowercased before lookup)
    it("accepts a lowercase method name", async () => {
        const svc = makeService();
        await expect(serviceRequest(svc, "post", "/p", { a: 1 })).resolves.toBe("POST");
        expect((svc as any).post).toHaveBeenCalledWith("/p", { a: 1 }, undefined);
    });

    // failure path — unknown method throws
    it("throws when the service has no matching method", async () => {
        const svc = makeService();
        await expect(serviceRequest(svc, "OPTIONS", "/x")).rejects.toThrow(
            "[serviceRequest] Service does not support method: OPTIONS",
        );
    });
});
