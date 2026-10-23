import { describe, expect, it } from "@rstest/core";
import { CONTROL_EVENTS, isKnownEvent } from "./events";

describe("CONTROL_EVENTS registry", () => {
    it("lists the P1 events for Input and Button", () => {
        expect(CONTROL_EVENTS.InputControl).toEqual(["onChange", "onInput", "onFocus", "onBlur"]);
        expect(CONTROL_EVENTS.ButtonControl).toEqual(["onClick"]);
    });
    it("isKnownEvent validates against a control's event list", () => {
        expect(isKnownEvent("InputControl", "onChange")).toBe(true);
        expect(isKnownEvent("InputControl", "onClick")).toBe(false);
        expect(isKnownEvent("UnknownControl", "onChange")).toBe(false);
    });
});
