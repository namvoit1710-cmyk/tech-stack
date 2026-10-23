import { describe, expect, it } from "@rstest/core";
import type { ActionScriptCtx, ScriptEvent } from "./action-script-ctx";

describe("ActionScriptCtx contract", () => {
    it("exposes the flat surface authors code against", () => {
        // Compile-time shape assertion; the object is never executed.
        const shape = (ctx: ActionScriptCtx): ScriptEvent => {
            ctx.setValue("a", 1);
            void ctx.gateway.invoke("app", "fn");
            ctx.toast("hi", "success");
            return ctx.event;
        };
        expect(typeof shape).toBe("function");
    });
});
