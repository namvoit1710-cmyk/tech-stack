import { describe, expect, it, rs } from "@rstest/core";
import { createFieldConfigActions } from "./field-config-actions";
import type { DesignerNode } from "../../../types";

function nodeWith(script?: Record<string, string>): DesignerNode {
    return { _id: "n1", key: "qty", outputType: "string", fieldConfig: { fieldControl: "InputControl", script } } as unknown as DesignerNode;
}

describe("createFieldConfigActions.setScript", () => {
    it("sets a script source for an event, preserving other events", () => {
        let updated: DesignerNode | undefined;
        const updateNode = rs.fn((_id: string, updater: (n: DesignerNode) => DesignerNode) => {
            updated = updater(nodeWith({ onFocus: "async function onFocus(ctx){}" }));
        });
        const actions = createFieldConfigActions(nodeWith({ onFocus: "async function onFocus(ctx){}" }), updateNode);
        actions.setScript("onChange", "async function onChange(ctx){ ctx.setValue('total', 1); }");
        expect(updateNode).toHaveBeenCalledWith("n1", expect.any(Function));
        expect(updated!.fieldConfig.script).toEqual({
            onFocus: "async function onFocus(ctx){}",
            onChange: "async function onChange(ctx){ ctx.setValue('total', 1); }",
        });
    });

    it("removes the event key when source is empty/whitespace", () => {
        let updated: DesignerNode | undefined;
        const updateNode = rs.fn((_id: string, updater: (n: DesignerNode) => DesignerNode) => {
            updated = updater(nodeWith({ onChange: "async function onChange(ctx){}", onBlur: "async function onBlur(ctx){}" }));
        });
        const actions = createFieldConfigActions(nodeWith({ onChange: "x", onBlur: "y" }), updateNode);
        actions.setScript("onChange", "   ");
        expect(updated!.fieldConfig.script).toEqual({ onBlur: "async function onBlur(ctx){}" });
    });
});
