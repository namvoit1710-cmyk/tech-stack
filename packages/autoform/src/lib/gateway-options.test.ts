import { describe, expect, it } from "@rstest/core";
import {
    buildGatewayOptionsServerConfig,
    GATEWAY_OPTIONS_DEFAULT_DATAPATH,
    GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH,
    GATEWAY_OPTIONS_DEFAULT_TOTALPATH,
    GATEWAY_OPTIONS_ENDPOINT,
    GATEWAY_OPTIONS_SERVICE,
    type IGatewayOptionsConfig,
    parseGatewayOptionsServerConfig,
} from "./gateway-options";

describe("buildGatewayOptionsServerConfig / parseGatewayOptionsServerConfig round-trip (SA-2184)", () => {
    it("builds a POST invoke serverOptions addressed by name, and round-trips it", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "sap-erp", functionName: "listPlants", functionId: "", dataPath: "data.result.body.items",
        };
        const so = buildGatewayOptionsServerConfig(cfg) as any;
        expect(so.service).toBe(GATEWAY_OPTIONS_SERVICE);
        expect(so.method).toBe("POST");
        expect(so.endpoint).toBe(GATEWAY_OPTIONS_ENDPOINT);
        expect(so.body).toEqual({ params: { app: "sap-erp", function: "listPlants", args: {} } });
        expect(so.responseMapping.dataPath).toBe("data.result.body.items");

        expect(parseGatewayOptionsServerConfig(so)).toEqual(cfg);
    });

    it("defaults the items path under the DOUBLE envelope the invoke actually returns", () => {
        const so = buildGatewayOptionsServerConfig({ appName: "a", functionName: "f", functionId: "", dataPath: "" }) as any;
        expect(so.responseMapping.dataPath).toBe(GATEWAY_OPTIONS_DEFAULT_DATAPATH);
    });

    it("heals a legacy 'result.*' items path", () => {
        const so = buildGatewayOptionsServerConfig({ appName: "a", functionName: "f", functionId: "", dataPath: "result.body.items" }) as any;
        expect(so.responseMapping.dataPath).toBe("data.result.body.items");
    });

    it("parseGatewayOptionsServerConfig ignores non-gateway serverOptions", () => {
        expect(parseGatewayOptionsServerConfig({ service: "workflow", endpoint: "gateway/apps" })).toBeNull();
        expect(parseGatewayOptionsServerConfig(null)).toBeNull();
    });

    it("still parses a serverOptions saved against the old worker-type name", () => {
        const legacy = {
            service: "workflow",
            endpoint: "workers/gateway-worker/functions/invoke_app_function/invoke",
            method: "POST",
            body: { params: { function_id: "fn-9", args: {} } },
            responseMapping: { dataPath: "result.body" },
            _gateway: { app: "sap-erp", function: "listPlants" },
        };
        const back = parseGatewayOptionsServerConfig(legacy);
        expect(back?.appName).toBe("sap-erp");
        expect(back?.functionName).toBe("listPlants");
        expect(back?.functionId).toBe("fn-9");
    });

    it("bakes locked param bindings into body.params.args, drops empty ones", () => {
        const so = buildGatewayOptionsServerConfig({
            appName: "file-service", functionName: "country_list", functionId: "", dataPath: "",
            bindings: [
                { key: "orderby", mode: "lock", constant: "name asc" },
                { key: "count", mode: "lock", constant: "" },
            ],
        }) as any;
        expect(so.body.params.args).toEqual({ orderby: "name asc" });
        expect(so.dependencies).toBeUndefined();
    });

    it("turns exposed param bindings into dependencies[] (cascading refetch)", () => {
        const so = buildGatewayOptionsServerConfig({
            appName: "file-service", functionName: "country_list", functionId: "", dataPath: "",
            bindings: [{ key: "region_id", mode: "expose" }],
        }) as any;
        expect(so.dependencies).toEqual(["region_id"]);
        expect(so.body.params.args).toEqual({});
    });

    it("round-trips mixed expose + lock bindings", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "file-service", functionName: "country_list", functionId: "", dataPath: "data.result.body",
            bindings: [
                { key: "region_id", mode: "expose" },
                { key: "orderby", mode: "lock", constant: "name asc" },
            ],
        };
        const so = buildGatewayOptionsServerConfig(cfg);
        const back = parseGatewayOptionsServerConfig(so);
        expect(back?.bindings).toEqual(expect.arrayContaining(cfg.bindings!));
        expect(back?.bindings).toHaveLength(2);
    });

    it("carries searchArgKey through build + parse (Combobox typeahead binding)", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "file-service", functionName: "country_list", functionId: "", dataPath: "", searchArgKey: "q",
        };
        const so = buildGatewayOptionsServerConfig(cfg) as any;
        expect(so.searchArgKey).toBe("q");
        expect(parseGatewayOptionsServerConfig(so)?.searchArgKey).toBe("q");
    });

    it("omits searchArgKey from the built config when not set (Select/RadioPopup — no typeahead)", () => {
        const so = buildGatewayOptionsServerConfig({ appName: "a", functionName: "f", functionId: "", dataPath: "" }) as any;
        expect(so.searchArgKey).toBeUndefined();
    });
});

describe("paged mode — SelectTable-only totalPath/contextValues (SA-2192)", () => {
    it("defaults the paged dataPath one level deeper than Phase A (rows under .data)", () => {
        const so = buildGatewayOptionsServerConfig(
            { appName: "a", functionName: "f", functionId: "", dataPath: "" },
            { paged: true }
        ) as any;
        expect(so.responseMapping.dataPath).toBe(GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH);
        expect(GATEWAY_OPTIONS_DEFAULT_PAGED_DATAPATH).toBe(`${GATEWAY_OPTIONS_DEFAULT_DATAPATH}.data`);
    });

    it("emits responseMapping.totalPath only when paged", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "a", functionName: "f", functionId: "", dataPath: "", totalPath: "data.result.body.total_count",
        };
        const paged = buildGatewayOptionsServerConfig(cfg, { paged: true }) as any;
        expect(paged.responseMapping.totalPath).toBe("data.result.body.total_count");

        const notPaged = buildGatewayOptionsServerConfig(cfg, { paged: false }) as any;
        expect(notPaged.responseMapping.totalPath).toBeUndefined();
    });

    it("round-trips totalPath through parse (paged only)", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "a", functionName: "f", functionId: "", dataPath: "", totalPath: GATEWAY_OPTIONS_DEFAULT_TOTALPATH,
        };
        const so = buildGatewayOptionsServerConfig(cfg, { paged: true });
        expect(parseGatewayOptionsServerConfig(so, { paged: true })?.totalPath).toBe(GATEWAY_OPTIONS_DEFAULT_TOTALPATH);
        // Not paged: the field is never surfaced back, even if a stray totalPath were present.
        expect(parseGatewayOptionsServerConfig(so, { paged: false })?.totalPath).toBeUndefined();
    });

    it("encodes contextValues bindings into one queryParams.contextValues JSON string, only when paged", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "a", functionName: "f", functionId: "", dataPath: "",
            contextValues: [{ apiKey: "tenant_id", formField: "tenant" }],
        };
        const so = buildGatewayOptionsServerConfig(cfg, { paged: true }) as any;
        expect(JSON.parse(decodeURIComponent(so.queryParams.contextValues))).toEqual({ tenant_id: "tenant" });

        const notPaged = buildGatewayOptionsServerConfig(cfg, { paged: false }) as any;
        expect(notPaged.queryParams).toBeUndefined();
    });

    it("round-trips contextValues bindings through parse (paged only)", () => {
        const cfg: IGatewayOptionsConfig = {
            appName: "a", functionName: "f", functionId: "", dataPath: "",
            contextValues: [
                { apiKey: "tenant_id", formField: "tenant" },
                { apiKey: "region_id", formField: "region" },
            ],
        };
        const so = buildGatewayOptionsServerConfig(cfg, { paged: true });
        const back = parseGatewayOptionsServerConfig(so, { paged: true });
        expect(back?.contextValues).toEqual(expect.arrayContaining(cfg.contextValues!));
        expect(back?.contextValues).toHaveLength(2);
    });

    it("drops an empty apiKey from the encoded contextValues mapping", () => {
        const so = buildGatewayOptionsServerConfig(
            { appName: "a", functionName: "f", functionId: "", dataPath: "", contextValues: [{ apiKey: "", formField: "tenant" }] },
            { paged: true }
        ) as any;
        expect(so.queryParams).toBeUndefined();
    });
});

// ─── pageArgKeys round-trip (SA-2192) ─────────────────────────────────────────

describe("gateway options — pageArgKeys", () => {
    const base = { appName: "file-service", functionName: "country_list", functionId: "", dataPath: "" };

    it("is not emitted when the author leaves the OData defaults", () => {
        const out = buildGatewayOptionsServerConfig(
            { ...base, pageArgKeys: { top: "$top", skip: "$skip", count: "$count" } },
            { paged: true }
        );
        expect(out.pageArgKeys).toBeUndefined();
    });

    it("emits only the slots that differ from the default", () => {
        const out = buildGatewayOptionsServerConfig(
            { ...base, pageArgKeys: { top: "limit", skip: "$skip" } },
            { paged: true }
        );
        expect(out.pageArgKeys).toEqual({ top: "limit" });
    });

    it("keeps an explicit empty name — that is 'do not send this arg'", () => {
        const out = buildGatewayOptionsServerConfig({ ...base, pageArgKeys: { count: "" } }, { paged: true });
        expect(out.pageArgKeys).toEqual({ count: "" });
    });

    it("is ignored for a non-paged (Phase A) source", () => {
        const out = buildGatewayOptionsServerConfig({ ...base, pageArgKeys: { top: "limit" } });
        expect(out.pageArgKeys).toBeUndefined();
    });

    it("round-trips through parse", () => {
        const built = buildGatewayOptionsServerConfig(
            { ...base, pageArgKeys: { top: "limit", skip: "offset", count: "" } },
            { paged: true }
        );
        const parsed = parseGatewayOptionsServerConfig(built);
        expect(parsed?.pageArgKeys).toEqual({ top: "limit", skip: "offset", count: "" });
    });
});
