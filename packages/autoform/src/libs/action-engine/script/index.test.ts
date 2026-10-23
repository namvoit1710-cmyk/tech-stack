import { describe, expect, it } from "@rstest/core";
import * as script from "./index";

describe("script barrel", () => {
    it("re-exports the public runtime surface", () => {
        expect(typeof script.makeScriptCtx).toBe("function");
        expect(typeof script.runFieldScript).toBe("function");
        expect(typeof script.useFieldScript).toBe("function");
        expect(typeof script.makeGatewayInvoke).toBe("function");
        expect(script.CONTROL_EVENTS.ButtonControl).toEqual(["onClick"]);
    });
});
