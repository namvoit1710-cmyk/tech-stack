import { describe, expect, it, rs } from "@rstest/core";
import type { EngineContext } from "../types";
import { makeScriptCtx } from "./make-script-ctx";

export function makeEngine(over: Partial<EngineContext> = {}): EngineContext {
    return {
        formValues: { price: 10 },
        methods: {
            setValue: rs.fn(),
            getValues: rs.fn().mockReturnValue({ price: 10 }),
            trigger: rs.fn().mockResolvedValue(true),
            setError: rs.fn(),
            reset: rs.fn(),
            clearErrors: rs.fn(),
        },
        payload: {},
        ui: {
            toast: rs.fn(),
            confirm: rs.fn().mockResolvedValue(true),
            redirect: rs.fn(),
            closeDialog: rs.fn(),
            refresh: rs.fn(),
            emitEvent: rs.fn(),
        },
        ...over,
    } as EngineContext;
}

describe("makeScriptCtx — core", () => {
    it("exposes the triggering event and live formValues", () => {
        const ctx = makeScriptCtx(makeEngine(), { type: "onChange", value: 5, name: "qty" });
        expect(ctx.event).toEqual({ type: "onChange", value: 5, name: "qty" });
        expect(ctx.formValues).toEqual({ price: 10 });
    });
    it("flattens RHF setValue/getValues", () => {
        const engine = makeEngine();
        const ctx = makeScriptCtx(engine, { type: "onChange" });
        ctx.setValue("total", 50, { shouldValidate: true });
        expect(engine.methods.setValue).toHaveBeenCalledWith("total", 50, { shouldValidate: true });
        ctx.getValues("price");
        expect(engine.methods.getValues).toHaveBeenCalledWith("price");
    });
    it("maps validate -> methods.trigger", async () => {
        const engine = makeEngine();
        await expect(makeScriptCtx(engine, { type: "onChange" }).validate(["a", "b"])).resolves.toBe(true);
        expect(engine.methods.trigger).toHaveBeenCalledWith(["a", "b"]);
    });
    it("adapts setError(field, message) -> methods.setError(field, { message })", () => {
        const engine = makeEngine();
        makeScriptCtx(engine, { type: "onChange" }).setError("qty", "Too big");
        expect(engine.methods.setError).toHaveBeenCalledWith("qty", { message: "Too big" });
    });
});

describe("makeScriptCtx — ui / gateway / row / refetch", () => {
    it("routes ui ops to engine.ui", async () => {
        const engine = makeEngine();
        const ctx = makeScriptCtx(engine, { type: "onClick" });
        ctx.toast("saved", "success");
        expect(engine.ui.toast).toHaveBeenCalledWith("saved", "success");
        ctx.emitToHost("done", { id: 1 });
        expect(engine.ui.emitEvent).toHaveBeenCalledWith("done", { id: 1 });
        await ctx.confirm("sure?", "Title");
        expect(engine.ui.confirm).toHaveBeenCalledWith("sure?", "Title");
    });
    it("wires ctx.gateway via engine.services", async () => {
        const post = rs.fn().mockResolvedValue({ result: { status_code: 200, body: { ok: true } } });
        const engine = makeEngine({ services: { workflow: { post } } as never });
        await expect(makeScriptCtx(engine, { type: "onClick" }).gateway.invoke("app", "fn", { x: 1 })).resolves.toEqual({ ok: true });
    });
    it("surfaces row context and forwards updateRow", () => {
        const updateRow = rs.fn();
        const engine = makeEngine({ rowId: "r1", rowIndex: 2, rowData: { a: 1 }, updateRow });
        const ctx = makeScriptCtx(engine, { type: "onChange" });
        expect(ctx.rowId).toBe("r1");
        expect(ctx.rowIndex).toBe(2);
        expect(ctx.rowData).toEqual({ a: 1 });
        ctx.updateRow({ a: 2 });
        expect(updateRow).toHaveBeenCalledWith({ a: 2 });
    });
    it("no-ops optional ops when the engine omits them", () => {
        const ctx = makeScriptCtx(makeEngine({ updateRow: undefined, updateRowById: undefined }), { type: "onChange" });
        expect(() => ctx.updateRow({ a: 1 })).not.toThrow();
        expect(() => ctx.updateRowById("r1", { a: 1 })).not.toThrow();
    });
    it("refetch(key) calls the keyed registry entry", async () => {
        const refetchFn = rs.fn().mockResolvedValue(undefined);
        const engine = makeEngine({ refetchRegistry: { get: rs.fn().mockReturnValue(refetchFn), getAll: rs.fn() } });
        await makeScriptCtx(engine, { type: "onChange" }).refetch("grid");
        expect(engine.refetchRegistry!.get).toHaveBeenCalledWith("grid");
        expect(refetchFn).toHaveBeenCalled();
    });
});
