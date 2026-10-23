/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../../../contexts/builder.context";
import { useComboboxOptions } from "./use-combobox-options";

afterEach(cleanup);

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

const tick = () => act(async () => { await Promise.resolve(); });
const debounceTick = () => act(async () => { await new Promise((r) => setTimeout(r, 350)); });

describe("useComboboxOptions — gateway (POST) serverOptions (SA-2184)", () => {
    const GATEWAY_SERVER_OPTIONS = {
        service: "workflow",
        endpoint: "workers/gateway/functions/invoke_app_function/invoke",
        method: "POST" as const,
        body: { params: { app: "file-service", function: "country_list", args: {} } },
    };

    it("POST branch forwards the typed term as args[searchArgKey] (default $search), not a query param", async () => {
        const postMock = rs.fn().mockResolvedValue({ data: { result: { body: [] } } });
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }) }
        );
        await tick();

        act(() => result.current.setInputValue("fra"));
        await debounceTick();

        const lastCall = postMock.mock.calls.at(-1)!;
        expect(lastCall[0]).toBe(GATEWAY_SERVER_OPTIONS.endpoint);
        expect(lastCall[1]).toEqual({
            params: { app: "file-service", function: "country_list", args: { $search: "fra" } },
        });
    });

    it("respects a custom searchArgKey instead of the $search default", async () => {
        const postMock = rs.fn().mockResolvedValue({ data: { result: { body: [] } } });
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: { ...GATEWAY_SERVER_OPTIONS, searchArgKey: "q" } as any }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }) }
        );
        act(() => result.current.setInputValue("term"));
        await debounceTick();

        const lastCall = postMock.mock.calls.at(-1)!;
        expect(lastCall[1].params.args).toEqual({ q: "term" });
    });

    it("merges dependency values into args alongside the search term", async () => {
        const postMock = rs.fn().mockResolvedValue({ data: { result: { body: [] } } });
        const { result } = renderHook(
            () => useComboboxOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, dependencies: ["region_id"] } as any,
            }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }, { region_id: "eu" }) }
        );
        await tick();
        act(() => result.current.setInputValue("fra"));
        await debounceTick();

        const lastCall = postMock.mock.calls.at(-1)!;
        expect(lastCall[1].params.args).toEqual({ region_id: "eu", $search: "fra" });
    });

    it("preserves locked constant args already in body.params.args", async () => {
        const postMock = rs.fn().mockResolvedValue({ data: { result: { body: [] } } });
        const so = {
            ...GATEWAY_SERVER_OPTIONS,
            body: { params: { app: "file-service", function: "country_list", args: { region_id: "eu" } } },
        };
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: so as any }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }) }
        );
        act(() => result.current.setInputValue("fra"));
        await debounceTick();

        const lastCall = postMock.mock.calls.at(-1)!;
        expect(lastCall[1].params.args).toEqual({ region_id: "eu", $search: "fra" });
    });

    it("unwraps options from data.result.body when no explicit dataPath is set (gateway default)", async () => {
        const postMock = rs.fn().mockResolvedValue({
            data: { result: { body: [{ id: "fr", value: "France" }] } },
        });
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: GATEWAY_SERVER_OPTIONS as any }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }) }
        );
        await tick();
        expect(result.current.filteredOptions).toEqual([{ id: "fr", value: "France" }]);
    });

    it("an explicit responseMapping.dataPath still wins over the gateway default", async () => {
        const postMock = rs.fn().mockResolvedValue({
            data: { result: { body: { rows: [{ id: "x", value: "X" }] } } },
        });
        const { result } = renderHook(
            () => useComboboxOptions({
                serverOptions: { ...GATEWAY_SERVER_OPTIONS, responseMapping: { dataPath: "data.result.body.rows" } } as any,
            }),
            { wrapper: makeWrapper({ workflow: { fetch: rs.fn(), post: postMock } }) }
        );
        await tick();
        expect(result.current.filteredOptions).toEqual([{ id: "x", value: "X" }]);
    });
});

describe("useComboboxOptions — legacy REST (GET) unchanged (SA-2184 backward compat)", () => {
    const REST_SERVER_OPTIONS = { service: "gateway", endpoint: "odata/Country/data" };

    it("GET with default mode still sends $search as a query param (not gateway args)", async () => {
        const fetchMock = rs.fn().mockResolvedValue({ data: { items: [] } });
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: REST_SERVER_OPTIONS as any }),
            { wrapper: makeWrapper({ gateway: { fetch: fetchMock, post: rs.fn() } }) }
        );
        act(() => result.current.setInputValue("fra"));
        await debounceTick();

        const lastCall = fetchMock.mock.calls.at(-1)!;
        expect(lastCall[1]).toEqual({ $search: "fra" });
    });

    it("defaults dataPath to data.items for GET (unchanged default)", async () => {
        const fetchMock = rs.fn().mockResolvedValue({ data: { items: [{ id: "1", value: "One" }] } });
        const { result } = renderHook(
            () => useComboboxOptions({ serverOptions: REST_SERVER_OPTIONS as any }),
            { wrapper: makeWrapper({ gateway: { fetch: fetchMock, post: rs.fn() } }) }
        );
        await tick();
        expect(result.current.filteredOptions).toEqual([{ id: "1", value: "One" }]);
    });

    it("no serverOptions → falls back to client-side filtering of static options", () => {
        const { result } = renderHook(
            () => useComboboxOptions({ options: [{ id: "a", value: "Apple" }, { id: "b", value: "Banana" }] }),
            { wrapper: makeWrapper() }
        );
        act(() => result.current.setInputValue("app"));
        expect(result.current.filteredOptions).toEqual([{ id: "a", value: "Apple" }]);
    });
});
