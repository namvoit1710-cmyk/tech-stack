import { describe, expect, it } from "@rstest/core";
import { ACTION_SCRIPT_CTX_DTS, CTX_HINTS } from "./action-script-ctx.dts";

describe("ACTION_SCRIPT_CTX_DTS", () => {
    it("is ambient (no export/import) so Monaco treats it as global types", () => {
        expect(ACTION_SCRIPT_CTX_DTS).not.toMatch(/\bexport\b/);
        expect(ACTION_SCRIPT_CTX_DTS).not.toMatch(/\bimport\b/);
    });
    it("declares ActionScriptCtx with the ctx surface + a global ctx", () => {
        for (const member of ["interface ActionScriptCtx", "event", "formValues", "setValue", "getValues", "gateway", "toast", "emitToHost", "refetch"]) {
            expect(ACTION_SCRIPT_CTX_DTS).toContain(member);
        }
        expect(ACTION_SCRIPT_CTX_DTS).toContain("declare const ctx: ActionScriptCtx");
    });
});

describe("CTX_HINTS", () => {
    it("exposes clickable ctx snippets covering the core surface", () => {
        const labels = CTX_HINTS.map((h) => h.label);
        expect(labels).toEqual(
            expect.arrayContaining([
                "ctx.event", "ctx.setValue(k,v)", "ctx.formValues",
                "await ctx.gateway.invoke(app,fn,args)", "ctx.toast()", "ctx.emitToHost()",
            ]),
        );
        for (const h of CTX_HINTS) expect(typeof h.snippet).toBe("string");
    });
});
