/// <reference types="@testing-library/jest-dom" />

import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../../../contexts/builder.context";
import { useSelectTableOptions } from "./use-select-table-options";

afterEach(cleanup);

// ─── Wrappers ────────────────────────────────────────────────────────────────

function makeWrapper(services?: Record<string, unknown>, defaultValues?: Record<string, unknown>) {
    const Wrapper = ({ children }: { children: React.ReactNode }) => {
        const methods = useForm({ defaultValues });
        return React.createElement(
            BuilderProvider,
            {
                value: {
                    schema: { fields: [] },
                    fieldComponent: {},
                    wrapperComponent: {},
                    services: services as any,
                },
            },
            React.createElement(FormProvider, methods as any, children)
        );
    };
    return Wrapper;
}

const STATIC_ROWS = [
    { id: "1", name: "Alice", role: "admin" },
    { id: "2", name: "Bob", role: "user" },
    { id: "3", name: "Charlie", role: "user" },
];

// ─── Static options ───────────────────────────────────────────────────────────

describe("useSelectTableOptions — static options", () => {
    it("returns all rows when not searching", () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        expect(result.current.data).toHaveLength(3);
    });

    it("client-filters by search across all field values", async () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        act(() => result.current.setSearchValue("ali"));
        expect(result.current.data).toHaveLength(1);
        expect(result.current.data[0]!.name).toBe("Alice");
    });

    it("filter is case-insensitive", async () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        act(() => result.current.setSearchValue("ALICE"));
        expect(result.current.data).toHaveLength(1);
    });

    it("returns empty when search matches nothing", () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        act(() => result.current.setSearchValue("xyz-no-match"));
        expect(result.current.data).toHaveLength(0);
        expect(result.current.totalCount).toBe(0);
    });

    it("totalCount reflects filtered length", () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        act(() => result.current.setSearchValue("user"));
        expect(result.current.totalCount).toBe(2);
    });

    it("paginates static options client-side", () => {
        const manyRows = Array.from({ length: 15 }, (_, i) => ({ id: String(i), name: `User${i}` }));
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: manyRows }),
            { wrapper: makeWrapper() }
        );
        expect(result.current.data).toHaveLength(10);
        expect(result.current.totalCount).toBe(15);

        act(() => result.current.setPagination({ pageIndex: 1, pageSize: 10 }));
        expect(result.current.data).toHaveLength(5);
    });

    it("loading is always false for static options", () => {
        const { result } = renderHook(
            () => useSelectTableOptions({ staticOptions: STATIC_ROWS }),
            { wrapper: makeWrapper() }
        );
        expect(result.current.loading).toBe(false);
    });
});

// ─── Server options ───────────────────────────────────────────────────────────

describe("useSelectTableOptions — server options", () => {
    let fetchMock: ReturnType<typeof rs.fn>;
    let services: Record<string, unknown>;

    beforeEach(() => {
        fetchMock = rs.fn();
        services = { myService: { fetch: fetchMock } };
    });

    const SERVER_OPTIONS = {
        service: "myService",
        endpoint: "/api/cities",
    };

    const mockResponse = (items: unknown[], count: number) =>
        Promise.resolve({ data: items, count });

    it("does not fetch when enabled=false", () => {
        renderHook(
            () => useSelectTableOptions({ serverOptions: SERVER_OPTIONS as any, enabled: false }),
            { wrapper: makeWrapper(services) }
        );
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("fetches on enabled=true and populates data", async () => {
        fetchMock.mockReturnValue(mockResponse(STATIC_ROWS, 3));
        const { result } = renderHook(
            () => useSelectTableOptions({ serverOptions: SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(fetchMock).toHaveBeenCalled();
        expect(result.current.data).toHaveLength(3);
        expect(result.current.totalCount).toBe(3);
    });

    it("passes $top, $skip, $count params", async () => {
        fetchMock.mockReturnValue(mockResponse([], 0));
        renderHook(
            () => useSelectTableOptions({ serverOptions: SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.$top).toBe(10);
        expect(params.$skip).toBe(0);
        expect(params.$count).toBe(true);
    });

    it("loading=true during fetch, false after resolve", async () => {
        let resolvePromise!: (v: unknown) => void;
        const deferred = new Promise((res) => { resolvePromise = res; });
        fetchMock.mockReturnValue(deferred);

        const { result } = renderHook(
            () => useSelectTableOptions({ serverOptions: SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        expect(result.current.loading).toBe(true);

        await act(async () => {
            resolvePromise({ data: [], count: 0 });
            await Promise.resolve();
        });
        expect(result.current.loading).toBe(false);
    });

    it("fetch error → data empty, loading false", async () => {
        fetchMock.mockReturnValue(Promise.reject(new Error("network")));
        const { result } = renderHook(
            () => useSelectTableOptions({ serverOptions: SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(result.current.data).toHaveLength(0);
        expect(result.current.loading).toBe(false);
    });

    it("uses custom responseMapping paths", async () => {
        fetchMock.mockReturnValue(Promise.resolve({ result: { list: STATIC_ROWS, total: 3 } }));
        const { result } = renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    responseMapping: { dataPath: "result.list", totalPath: "result.total" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(result.current.data).toHaveLength(3);
        expect(result.current.totalCount).toBe(3);
    });

    it("odata mode with search builds $filter with contains", async () => {
        fetchMock.mockReturnValue(mockResponse([], 0));
        const { result } = renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    mode: "odata",
                    odataConfig: { searchField: "CityName" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });

        act(() => result.current.setSearchValue("hanoi"));
        await act(async () => {
            await new Promise((r) => setTimeout(r, 350));
        });

        const lastCall = fetchMock.mock.calls.at(-1)!;
        const params = lastCall[1] as Record<string, unknown>;
        expect(params.$filter).toContain("contains");
        expect(params.$filter).toContain("hanoi");
    });

    it("odata mode applies defaultFilter when no search", async () => {
        fetchMock.mockReturnValue(mockResponse([], 0));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    mode: "odata",
                    odataConfig: { defaultFilter: "IsActive eq true" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.$filter).toBe("IsActive eq true");
    });

    it("dependency not met → skips fetch, returns empty", async () => {
        fetchMock.mockReturnValue(mockResponse(STATIC_ROWS, 3));
        const { result } = renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...SERVER_OPTIONS, dependencies: ["countryId"] } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { countryId: "" }) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(fetchMock).not.toHaveBeenCalled();
        expect(result.current.data).toHaveLength(0);
    });

    it("dependency met → fetches with dep value in params", async () => {
        fetchMock.mockReturnValue(mockResponse(STATIC_ROWS, 3));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...SERVER_OPTIONS, dependencies: ["countryId"] } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { countryId: "VN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.countryId).toBe("VN");
    });
});

// ─── queryParams ──────────────────────────────────────────────────────────────

describe("useSelectTableOptions — queryParams", () => {
    let fetchMock: ReturnType<typeof rs.fn>;
    let services: Record<string, unknown>;

    beforeEach(() => {
        fetchMock = rs.fn().mockResolvedValue({ data: [], count: 0 });
        services = { myService: { fetch: fetchMock } };
    });

    const SERVER_OPTIONS = { service: "myService", endpoint: "/api/items" };

    it("merges queryParams into every request", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { templateId: "abc-123", "$orderby": "seq asc" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.templateId).toBe("abc-123");
        expect(params["$orderby"]).toBe("seq asc");
    });

    it("pagination $top/$skip override queryParams values", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { $top: 99, $skip: 99 },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.$top).toBe(10);
        expect(params.$skip).toBe(0);
    });

    it("queryParams without contextValues sends no contextValues key", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { templateId: "abc" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params).not.toHaveProperty("contextValues");
    });
});

// ─── contextValues ────────────────────────────────────────────────────────────

describe("useSelectTableOptions — contextValues (inside queryParams)", () => {
    let fetchMock: ReturnType<typeof rs.fn>;
    let services: Record<string, unknown>;

    beforeEach(() => {
        fetchMock = rs.fn().mockResolvedValue({ data: [], count: 0 });
        services = { myService: { fetch: fetchMock } };
    });

    const SERVER_OPTIONS = { service: "myService", endpoint: "/api/cities" };

    // %7B%22country_1%22%3A%20%22country%22%7D = {"country_1": "country"}
    const ENCODED_CONTEXT = "%7B%22country_1%22%3A%20%22country%22%7D";

    it("URL-encoded JSON in queryParams.contextValues: decodes and resolves form field value", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { contextValues: ENCODED_CONTEXT },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { country: "VN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.contextValues).toBe('{"country_1":"VN"}');
    });

    it("raw contextValues string NOT forwarded — only resolved JSON sent", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { templateId: "abc", contextValues: ENCODED_CONTEXT },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { country: "VN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        // must be resolved JSON, not the raw encoded string
        expect(params.contextValues).toBe('{"country_1":"VN"}');
        expect(params.contextValues).not.toBe(ENCODED_CONTEXT);
    });

    it("missing form field value → empty string in resolved JSON", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { contextValues: ENCODED_CONTEXT },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, {}) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.contextValues).toBe('{"country_1":""}');
    });

    it("multiple contextValues entries all resolved", async () => {
        // {"country_1":"country","region_1":"region"}
        const multiEncoded = encodeURIComponent(JSON.stringify({ country_1: "country", region_1: "region" }));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { contextValues: multiEncoded },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { country: "VN", region: "HN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.contextValues).toBe('{"country_1":"VN","region_1":"HN"}');
    });

    it("queryParams without contextValues → no contextValues param sent", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    queryParams: { templateId: "abc" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params).not.toHaveProperty("contextValues");
    });

    it("no queryParams at all → no contextValues param sent", async () => {
        renderHook(
            () => useSelectTableOptions({
                serverOptions: SERVER_OPTIONS as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params).not.toHaveProperty("contextValues");
    });

    it("full config: queryParams + contextValues + dependency + pagination all merged correctly", async () => {
        fetchMock.mockResolvedValue({ data: [], count: 0 });
        renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...SERVER_OPTIONS,
                    dependencies: ["country"],
                    queryParams: {
                        templateId: "019ed482",
                        "$orderby": "rowSeq asc",
                        contextValues: ENCODED_CONTEXT,
                    },
                    responseMapping: { dataPath: "data", totalPath: "count" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { country: "VN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const params = fetchMock.mock.calls[0]![1] as Record<string, unknown>;
        expect(params.templateId).toBe("019ed482");
        expect(params["$orderby"]).toBe("rowSeq asc");
        expect(params.country).toBe("VN");
        expect(params.$top).toBe(10);
        expect(params.$skip).toBe(0);
        expect(params.$count).toBe(true);
        expect(params.contextValues).toBe('{"country_1":"VN"}');
    });
});

// ─── gateway (POST) serverOptions (SA-2192) ───────────────────────────────────

describe("useSelectTableOptions — gateway (POST) serverOptions (SA-2192)", () => {
    let postMock: ReturnType<typeof rs.fn>;
    let fetchMock: ReturnType<typeof rs.fn>;
    let services: Record<string, unknown>;

    const GATEWAY_SERVER_OPTIONS = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST" as const,
        body: { params: { app: "file-service", function: "city_list", args: {} } },
    };

    const PAGED_RESPONSE = (rows: unknown[], totalCount: number) =>
        Promise.resolve({ data: { result: { body: { data: rows, total_count: totalCount } } } });

    beforeEach(() => {
        postMock = rs.fn();
        fetchMock = rs.fn();
        services = { workflow: { fetch: fetchMock, post: postMock } };
    });

    it("does not call .fetch — the gateway branch goes through .post", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE(STATIC_ROWS, 3));
        renderHook(
            () => useSelectTableOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(postMock).toHaveBeenCalled();
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("packs $top/$skip/$count (as the string \"true\") under body.params.args", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        renderHook(
            () => useSelectTableOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });

        const [endpoint, body] = postMock.mock.calls[0]!;
        expect(endpoint).toBe(GATEWAY_SERVER_OPTIONS.endpoint);
        expect(body.params.args).toEqual({ $top: 10, $skip: 0, $count: "true" });
    });

    it("pageArgKeys renames the page args to the upstream's own parameter names", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, pageArgKeys: { top: "limit", skip: "offset", count: "" } } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });

        // the upstream's names carry the values; the OData spellings are gone entirely,
        // and an empty name means the arg is not sent at all
        const [, body] = postMock.mock.calls[0]!;
        expect(body.params.args).toEqual({ limit: 10, offset: 0 });
    });

    it("honours searchArgKey on the gateway path instead of the literal $search", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        const { result } = renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, searchArgKey: "name_contains" } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        act(() => result.current.setSearchValue("han"));
        await waitFor(() => {
            const args = postMock.mock.calls.at(-1)![1].params.args;
            expect(args.name_contains).toBe("han");
            expect(args.$search).toBeUndefined();
        });
    });

    it("unwraps the default paged envelope: data.result.body.data + data.result.body.total_count", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE(STATIC_ROWS, 3));
        const { result } = renderHook(
            () => useSelectTableOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(result.current.data).toHaveLength(3);
        expect(result.current.totalCount).toBe(3);
    });

    it("search term rides args.$search, debounced, alongside pagination", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        const { result } = renderHook(
            () => useSelectTableOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });

        act(() => result.current.setSearchValue("hanoi"));
        await act(async () => { await new Promise((r) => setTimeout(r, 350)); });

        const lastCall = postMock.mock.calls.at(-1)!;
        expect(lastCall[1].params.args).toEqual({ $top: 10, $skip: 0, $count: "true", $search: "hanoi" });
    });

    it("dependency values merge into args", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE(STATIC_ROWS, 3));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, dependencies: ["countryId"] } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { countryId: "VN" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const args = postMock.mock.calls[0]![1].params.args;
        expect(args.countryId).toBe("VN");
    });

    it("resolved contextValues (queryParams) is forwarded as one arg under body.params.args", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        const ENCODED_CONTEXT = encodeURIComponent(JSON.stringify({ tenant_id: "tenant" }));
        renderHook(
            () => useSelectTableOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, queryParams: { contextValues: ENCODED_CONTEXT } } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services, { tenant: "acme" }) }
        );
        await act(async () => { await Promise.resolve(); });
        const args = postMock.mock.calls[0]![1].params.args;
        expect(args.contextValues).toBe('{"tenant_id":"acme"}');
    });

    it("preserves locked constant args already in body.params.args", async () => {
        postMock.mockReturnValue(PAGED_RESPONSE([], 0));
        const so = {
            ...GATEWAY_SERVER_OPTIONS,
            body: { params: { app: "file-service", function: "city_list", args: { region_id: "eu" } } },
        };
        renderHook(
            () => useSelectTableOptions({ serverOptions: so as any, enabled: true }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        const args = postMock.mock.calls[0]![1].params.args;
        expect(args.region_id).toBe("eu");
    });

    it("an explicit responseMapping still overrides the paged defaults", async () => {
        postMock.mockReturnValue(Promise.resolve({ result: { rows: STATIC_ROWS, matched: 3 } }));
        const { result } = renderHook(
            () => useSelectTableOptions({
                serverOptions: {
                    ...GATEWAY_SERVER_OPTIONS,
                    responseMapping: { dataPath: "result.rows", totalPath: "result.matched" },
                } as any,
                enabled: true,
            }),
            { wrapper: makeWrapper(services) }
        );
        await act(async () => { await Promise.resolve(); });
        expect(result.current.data).toHaveLength(3);
        expect(result.current.totalCount).toBe(3);
    });
});

// ─── error state ──────────────────────────────────────────────────────────────

describe("useSelectTableOptions — error state", () => {
    it("sets error when fetch rejects", async () => {
        const fetchMock = rs.fn().mockRejectedValueOnce(new Error("404 Not Found"));
        const { result } = renderHook(() =>
            useSelectTableOptions({
                serverOptions: {
                    endpoint: "/items",
                    service: "governance",
                },
                enabled: true,
            }),
            {
                wrapper: makeWrapper({ governance: { fetch: fetchMock } }),
            }
        );

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.error).toBe("404 Not Found");
    });

    it("clears error on successful refetch", async () => {
        const fetchMock = rs.fn()
            .mockRejectedValueOnce(new Error("404 Not Found"))
            .mockResolvedValueOnce({ data: [{ id: "1" }], count: 1 });

        const { result } = renderHook(() =>
            useSelectTableOptions({
                serverOptions: {
                    endpoint: "/items",
                    service: "governance",
                },
                enabled: true,
            }),
            {
                wrapper: makeWrapper({ governance: { fetch: fetchMock } }),
            }
        );

        await waitFor(() => expect(result.current.error).toBe("404 Not Found"));

        act(() => result.current.setSearchValue("x"));
        await waitFor(() => expect(result.current.error).toBeNull());
    });
});
