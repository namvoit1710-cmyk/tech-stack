import { describe, expect, it } from "@rstest/core";
import type { IGatewayFunction } from "@/features/gateway-admin/types/gateway";
import {
    buildGatewayBinding,
    buildGatewayOptionsServerConfig,
    gatewayFunctionMarker,
    isGatewayWorker,
    parseGatewayMarker,
    parseGatewayOptionsServerConfig,
    type IGatewayOptionsConfig,
    type IParamBinding,
} from "./gateway-binding";

// The builders return Record<string, unknown> (they feed autoform's untyped
// controlProps). Narrow once here so the assertions below stay type-safe.
interface IServerOptionsShape {
    method: string;
    endpoint: string;
    body: { params: { app?: string; function?: string; function_id?: string; args?: unknown } };
    responseMapping: { dataPath: string };
}
const buildSO = (cfg: IGatewayOptionsConfig): IServerOptionsShape =>
    buildGatewayOptionsServerConfig(cfg) as unknown as IServerOptionsShape;

interface IMarkerShape {
    gateway_result: {
        _function_call: {
            worker_function: string;
            params: { app?: string; function?: string; function_id?: string; args: Record<string, unknown> };
        };
    };
}
const buildMarker = (f: IGatewayFunction, b: IParamBinding[], appName = ""): IMarkerShape =>
    gatewayFunctionMarker(f, b, appName) as unknown as IMarkerShape;

const fn = {
    id: "fn-123",
    name: "createPO",
    input_params: [
        { key: "material", type: "string", location: "body", required: true, label: "Material" },
        { key: "qty", type: "number", location: "body", required: true, label: "Quantity" },
        { key: "plant", type: "string", location: "query", required: false, label: "Plant" },
    ],
    output_fields: [{ key: "po_number", type: "string", label: "PO Number" }],
} as unknown as IGatewayFunction;

describe("gateway-binding", () => {
    it("flags the gateway base type under EVERY name it is deployed as", () => {
        // deployment.yml sets WORKER_TYPE=gateway; config.py defaults to gateway-worker.
        // The old `.includes("gateway-worker")` returned false for the DEPLOYED name,
        // which hid the Gateway Action tab everywhere — and the test never noticed,
        // because it only ever passed the literal "gateway-worker".
        expect(isGatewayWorker("gateway")).toBe(true);
        expect(isGatewayWorker("gateway-worker")).toBe(true);
        expect(isGatewayWorker("worker:gateway")).toBe(true); // palette prefixes ids
        expect(isGatewayWorker("GATEWAY")).toBe(true);
        expect(isGatewayWorker("llm-worker")).toBe(false);
        expect(isGatewayWorker("gateway-ish")).toBe(false);   // exact match, not substring
        expect(isGatewayWorker(undefined)).toBe(false);
    });

    it("emits an invoke_app_function marker addressed by (app, function) name", () => {
        // NOT by function id: ids are minted per-environment, so an id-pinned
        // definition cannot be promoted dev → qa → prod.
        const marker = buildMarker(fn, [], "sap-erp");
        const call = marker.gateway_result._function_call;
        expect(call.worker_function).toBe("invoke_app_function");
        expect(call.params.app).toBe("sap-erp");
        expect(call.params.function).toBe("createPO");
        expect(call.params.function_id).toBeUndefined();
    });

    it("maps exposed params to {{$custom.key}} expressions (no spaces)", () => {
        const marker: any = gatewayFunctionMarker(fn, []); // default = expose all
        expect(marker.gateway_result._function_call.params.args).toEqual({
            material: "{{$custom.material}}",
            qty: "{{$custom.qty}}",
            plant: "{{$custom.plant}}",
        });
    });

    it("bakes locked constants, typed by param, and drops empty ones", () => {
        const bindings: IParamBinding[] = [
            { key: "plant", mode: "lock", constant: "1000" },
            { key: "qty", mode: "lock", constant: "5" },
            { key: "material", mode: "lock", constant: "" }, // empty → omitted
        ];
        const args: any = (gatewayFunctionMarker(fn, bindings) as any).gateway_result._function_call.params.args;
        expect(args.plant).toBe("1000");   // string param → string
        expect(args.qty).toBe(5);          // number param → number
        expect("material" in args).toBe(false);
    });

    it("derives input fields from exposed params and output fields from output_fields", () => {
        const bindings: IParamBinding[] = [{ key: "plant", mode: "lock", constant: "1000" }];
        const { inputFields, outputFields } = buildGatewayBinding(fn, bindings);
        expect(inputFields.map((f) => f.key)).toEqual(["material", "qty"]); // plant is locked, not exposed
        expect(inputFields.find((f) => f.key === "qty")?.fieldControl).toBe("NumberControl");
        expect(inputFields.find((f) => f.key === "material")?.required).toBe(true);
        // declared body field + status_code/error for branching
        expect(outputFields.map((f) => f.key)).toEqual(["po_number", "status_code", "error"]);
    });

    it("lifts the API body into declared outputs and surfaces status_code/error", () => {
        // raw worker output = { gateway_result: { status_code, headers, body, error } }; body is the API result.
        const { outputMapping } = buildGatewayBinding(fn, []);
        expect(outputMapping).toEqual([
            { key: "po_number", value: "{{$_output.gateway_result.body.po_number}}" },
            { key: "status_code", value: "{{$_output.gateway_result.status_code}}" },
            { key: "error", value: "{{$_output.gateway_result.error}}" },
        ]);
    });

    it("exposes the whole API body as `response` when the function declares no outputs", () => {
        const noOut = { ...fn, output_fields: [] } as unknown as IGatewayFunction;
        const { outputFields, outputMapping } = buildGatewayBinding(noOut, []);
        expect(outputFields.map((f) => f.key)).toEqual(["response", "status_code", "error"]);
        expect(outputMapping[0]).toEqual({ key: "response", value: "{{$_output.gateway_result.body}}" });
    });

    it("round-trips the name-addressed marker via parseGatewayMarker", () => {
        const bindings: IParamBinding[] = [
            { key: "plant", mode: "lock", constant: "1000" },
            // material + qty default to expose
        ];
        const marker = buildGatewayBinding(fn, bindings, "sap-erp").marker;

        const parsed = parseGatewayMarker(marker);
        expect(parsed?.app).toBe("sap-erp");
        expect(parsed?.function).toBe("createPO");
        // exposed args reverse to "expose"; locked constant reverses to "lock" + value
        expect(parsed?.bindings).toEqual([
            { key: "material", mode: "expose" },
            { key: "qty", mode: "expose" },
            { key: "plant", mode: "lock", constant: "1000" },
        ]);
    });

    it("still reads a marker saved by an older, id-addressed build", () => {
        // Without this fallback, opening an existing node would silently blank its pickers.
        const legacy = {
            gateway_result: {
                _function_call: {
                    worker_function: "invoke_app_function",
                    params: { function_id: "fn-123", args: { material: "{{$custom.material}}" } },
                    _binding: { app: "sap-erp", function: "createPO" },
                },
            },
        };
        const parsed = parseGatewayMarker(legacy);
        expect(parsed?.app).toBe("sap-erp");
        expect(parsed?.function).toBe("createPO");
        expect(parsed?.functionId).toBe("fn-123");
        expect(parsed?.bindings).toEqual([{ key: "material", mode: "expose" }]);
    });

    it("parseGatewayMarker returns null for empty / non-gateway markers", () => {
        expect(parseGatewayMarker(null)).toBeNull();
        expect(parseGatewayMarker({ x: { _function_call: { worker_function: "other", params: {} } } })).toBeNull();
    });

    it("builds a POST invoke serverOptions addressed by name, and round-trips it", () => {
        const cfg = { appName: "sap-erp", functionName: "listPlants", functionId: "", dataPath: "data.result.body.items" };
        const so = buildSO(cfg);
        expect(so.method).toBe("POST");
        // the DEPLOYED worker type is "gateway" — "gateway-worker" 400s with "Worker not found"
        expect(so.endpoint).toBe("workers/gateway/functions/invoke_app_function/invoke");
        expect(so.body).toEqual({ params: { app: "sap-erp", function: "listPlants", args: {} } });
        expect(so.responseMapping.dataPath).toBe("data.result.body.items");

        const back = parseGatewayOptionsServerConfig(so);
        expect(back).toEqual(cfg);
    });

    it("defaults the items path under the DOUBLE envelope the invoke actually returns", () => {
        // {status:ok, data:{status:success, result:{status_code, headers, body}}}
        // The old default "result.body" resolved to undefined → a silently empty dropdown.
        const so = buildSO({ appName: "a", functionName: "f", functionId: "", dataPath: "" });
        expect(so.responseMapping.dataPath).toBe("data.result.body");
    });

    it("heals a legacy 'result.*' items path, which could never have worked", () => {
        const so = buildSO({ appName: "a", functionName: "f", functionId: "", dataPath: "result.body.items" });
        expect(so.responseMapping.dataPath).toBe("data.result.body.items");
    });

    it("leaves an already-correct or custom items path alone", () => {
        const so = buildSO({ appName: "a", functionName: "f", functionId: "", dataPath: "data.result.body.rows" });
        expect(so.responseMapping.dataPath).toBe("data.result.body.rows");
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

    it("parseGatewayOptionsServerConfig ignores non-gateway serverOptions", () => {
        expect(parseGatewayOptionsServerConfig({ service: "workflow", endpoint: "gateway/apps" })).toBeNull();
        expect(parseGatewayOptionsServerConfig(null)).toBeNull();
    });

    it("does not duplicate status_code/error if the function declares them", () => {
        const collide = {
            ...fn,
            output_fields: [{ key: "status_code", type: "number", label: "Code" }],
        } as unknown as IGatewayFunction;
        const keys = buildGatewayBinding(collide, []).outputFields.map((f) => f.key);
        expect(keys).toEqual(["status_code", "error"]); // declared status_code wins, only error appended
    });
});
