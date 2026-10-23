import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import type { EngineContext } from "../types";
import type { IField } from "../../../types/schema";
import { clearScriptCache, compileScript, runFieldScript } from "./run-field-script";

function makeEngine(over: Partial<EngineContext> = {}): EngineContext {
    return {
        formValues: { price: 10 },
        methods: {
            setValue: rs.fn(), getValues: rs.fn().mockReturnValue({ price: 10 }),
            trigger: rs.fn().mockResolvedValue(true), setError: rs.fn(), reset: rs.fn(), clearErrors: rs.fn(),
        },
        payload: {},
        ui: { toast: rs.fn(), confirm: rs.fn(), redirect: rs.fn() },
        ...over,
    } as EngineContext;
}
const field = (script?: Record<string, string>): IField => ({
    key: "qty", outputType: "number", fieldConfig: { fieldControl: "InputControl", script },
});

beforeEach(() => clearScriptCache());

describe("runFieldScript — core", () => {
    it("no-ops when the field has no script for the event", async () => {
        const engine = makeEngine();
        await runFieldScript(field(), "onChange", { value: 5 }, engine);
        expect(engine.methods.setValue).not.toHaveBeenCalled();
    });
    it("runs the async handler with a flat ctx (setValue + event.value)", async () => {
        const engine = makeEngine();
        const src = "async function onChange(ctx) { ctx.setValue('total', Number(ctx.event.value) * 2); }";
        await runFieldScript(field({ onChange: src }), "onChange", { value: 5 }, engine);
        expect(engine.methods.setValue).toHaveBeenCalledWith("total", 10, undefined);
    });
    it("compileScript caches by source (compiles once)", () => {
        const src = "async function onChange(ctx){}";
        expect(compileScript(src)).toBe(compileScript(src));
    });
});

describe("runFieldScript — gateway + errors", () => {
    it("awaits ctx.gateway.invoke and applies the result", async () => {
        const post = rs.fn().mockResolvedValue({ result: { status_code: 200, body: { status: "OK" } } });
        const engine = makeEngine({ services: { workflow: { post } } as never });
        const src = "async function onClick(ctx){ const r = await ctx.gateway.invoke('wf','submit', ctx.formValues); ctx.setValue('status', r.status); }";
        await runFieldScript(field({ onClick: src }), "onClick", {}, engine);
        expect(post).toHaveBeenCalledWith(
            "workers/gateway/functions/invoke_app_function/invoke",
            { params: { app: "wf", function: "submit", args: { price: 10 } } },
        );
        expect(engine.methods.setValue).toHaveBeenCalledWith("status", "OK", undefined);
    });
    it("catches a thrown handler and surfaces it via ctx.toast(error)", async () => {
        const engine = makeEngine();
        await runFieldScript(field({ onChange: "async function onChange(ctx){ throw new Error('boom'); }" }), "onChange", {}, engine);
        expect(engine.ui.toast).toHaveBeenCalledWith("boom", "error");
    });
});
