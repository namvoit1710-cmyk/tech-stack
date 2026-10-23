import { renderHook } from "@testing-library/react";
import { describe, expect, it, rs } from "@rstest/core";
import { useBuilderServices } from "./use-builder-services";

const { mockCreateApiSdk, mockSdkInstance } = rs.hoisted(() => {
    const mockSdkInstance = {
        get: rs.fn(),
        post: rs.fn(),
        put: rs.fn(),
        patch: rs.fn(),
        delete: rs.fn(),
    };
    return {
        mockSdkInstance,
        mockCreateApiSdk: rs.fn().mockReturnValue(mockSdkInstance),
    };
});

rs.mock("@ldc/api-sdk", () => ({
    createApiSdk: (...args: unknown[]) => mockCreateApiSdk(...args),
}));

describe("useBuilderServices", () => {
    it("creates one sdk instance per service", () => {
        renderHook(() =>
            useBuilderServices({
                governance: { baseUrl: "https://gov.example.com", basePath: "api/v1" },
                odata: { baseUrl: "https://odata.example.com", basePath: "api/v1" },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledTimes(2);
    });

    it("passes baseURL as joinBaseUrl(baseUrl, basePath)", () => {
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1" },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ baseURL: "https://example.com/api/v1" }),
        );
    });

    it("passes withCredentials from ServiceConfig", () => {
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1", withCredentials: true },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ withCredentials: true }),
        );
    });

    it("passes csrfUrl from ServiceConfig to createApiSdk", () => {
        renderHook(() =>
            useBuilderServices({
                svc: {
                    baseUrl: "https://example.com",
                    basePath: "api/v1",
                    csrfUrl: "https://example.com/csrf.json",
                },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ csrfUrl: "https://example.com/csrf.json" }),
        );
    });

    it("passes getDevToken from ServiceConfig to createApiSdk", () => {
        const getDevToken = () => "my-token";
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1", getDevToken },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ getDevToken }),
        );
    });

    it("does NOT pass csrfUrl when absent", () => {
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1" },
            }),
        );
        const call = mockCreateApiSdk.mock.calls[0]?.[0] as Record<string, unknown>;
        expect(call?.csrfUrl).toBeUndefined();
    });

    it("does NOT pass getDevToken when absent", () => {
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1" },
            }),
        );
        const call = mockCreateApiSdk.mock.calls[0]?.[0] as Record<string, unknown>;
        expect(call?.getDevToken).toBeUndefined();
    });

    it("passes custom headers from ServiceConfig", () => {
        renderHook(() =>
            useBuilderServices({
                svc: {
                    baseUrl: "https://example.com",
                    basePath: "api/v1",
                    headers: { Authorization: "Bearer tok" },
                },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
        );
    });

    it("returns a ServiceHandler with fetch/post/put/patch/delete per service", () => {
        const { result } = renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1" },
            }),
        );
        expect(result.current.svc).toBeDefined();
        expect(typeof result.current.svc?.fetch).toBe("function");
        expect(typeof result.current.svc?.post).toBe("function");
        expect(typeof result.current.svc?.put).toBe("function");
        expect(typeof result.current.svc?.patch).toBe("function");
        expect(typeof result.current.svc?.delete).toBe("function");
    });

    it("passes onSessionExpired from ServiceConfig to createApiSdk", () => {
        const onSessionExpired = rs.fn();
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1", onSessionExpired },
            }),
        );
        expect(mockCreateApiSdk).toHaveBeenCalledWith(
            expect.objectContaining({ onSessionExpired }),
        );
    });

    it("does NOT pass onSessionExpired when absent", () => {
        renderHook(() =>
            useBuilderServices({
                svc: { baseUrl: "https://example.com", basePath: "api/v1" },
            }),
        );
        const call = mockCreateApiSdk.mock.calls[0]?.[0] as Record<string, unknown>;
        expect(call?.onSessionExpired).toBeUndefined();
    });
});
