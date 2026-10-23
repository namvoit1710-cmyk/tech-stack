/// <reference types="@testing-library/jest-dom" />
import { useState } from "react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DesignerProvider } from "../../../hooks/use-designer-context";
import type { DesignerNode } from "../../../types";
import { FieldConfigForm } from "./field-config-form";

rs.mock("../../../../field-control/code-control", () => ({
    __esModule: true,
    default: function MockCodeEditor({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
        return <textarea data-testid="code-editor" value={value ?? ""} onChange={(e) => onChange?.(e.target.value)} />;
    },
}));

// EventsEditor (now wired into FieldConfigForm, SA-2177) mounts its EventCodeEditor for any
// control with events — real @monaco-editor/react tries to fetch its loader script from a CDN
// and throws under happy-dom. Stub it exactly like events-editor.test.tsx / event-code-editor.test.tsx.
rs.mock("@monaco-editor/react", () => ({
    default: (props: { value: string; onChange?: (v: string) => void }) => (
        <textarea aria-label="code" value={props.value} onChange={(e) => props.onChange?.(e.target.value)} />
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

function fieldNode(fieldControl: string, controlProps: Record<string, unknown> = {}): DesignerNode {
    return {
        _id: "f1",
        _kind: "field",
        key: "pick",
        outputType: "string",
        default: fieldControl === "CheckBoxControl" ? false : "",
        fieldConfig: { fieldWrapper: "FormItemWrapper", fieldControl, controlProps, rules: [], condition: null },
    } as unknown as DesignerNode;
}

function captureUpdate(node: DesignerNode) {
    const calls: DesignerNode[] = [];
    const updateNode = rs.fn((_id: string, fn: (n: DesignerNode) => DesignerNode) => {
        calls.push(fn(node));
    });
    return { updateNode, calls };
}

const renderForm = (node: DesignerNode, updateNode = rs.fn()) =>
    render(
        <DesignerProvider value={{ nodes: [node] } as never}>
            <FieldConfigForm node={node} updateNode={updateNode} />
        </DesignerProvider>
    );

/**
 * Controlled harness (mirrors `FieldHarness` in field-config-form.test.tsx): `updateNode`
 * actually flows into React state and re-renders `FieldConfigForm` with the updated node —
 * unlike `captureUpdate` above, which only records calls and never re-renders. Needed to
 * catch a DISPLAY bug (a control that keeps showing a stale value after a re-render), which
 * a non-re-rendering harness cannot see even when the underlying write is correct.
 */
function ChoiceHarness({ initial }: { initial: DesignerNode }) {
    const [node, setNode] = useState<DesignerNode>(initial);
    const updateNode = (_id: string, fn: (n: DesignerNode) => DesignerNode) => setNode((p) => fn(p));
    return (
        <DesignerProvider value={{ nodes: [node] } as never}>
            <FieldConfigForm node={node} updateNode={updateNode} />
        </DesignerProvider>
    );
}

describe("FieldConfigForm — Choice controls", () => {
    it("Checkbox: default value is a boolean Switch, not a free-text input", async () => {
        const user = userEvent.setup();
        renderForm(fieldNode("CheckBoxControl"));
        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        expect(screen.getByRole("switch")).toBeInTheDocument();
        expect(screen.queryByPlaceholderText("default value")).not.toBeInTheDocument();
    });

    it("Select: shows the Data source selector", () => {
        renderForm(fieldNode("SelectControl", { options: [{ id: "a", value: "Apple" }] }));
        expect(screen.getByRole("radiogroup", { name: "Data source" })).toBeInTheDocument();
    });

    it("Combobox: toggling `multiple` writes multiple:true AND outputType:'array'", async () => {
        const node = fieldNode("ComboboxControl", {});
        const { updateNode, calls } = captureUpdate(node);
        const user = userEvent.setup();
        renderForm(node, updateNode);

        await user.click(screen.getByRole("switch", { name: "Multiple" }));

        expect(calls[0]?.fieldConfig.controlProps?.multiple).toBe(true);
        expect(calls[0]?.outputType).toBe("array");
    });

    it("Combobox: the Output type <Select> reflects 'array' immediately after toggling multiple — no reselect needed", async () => {
        const user = userEvent.setup();
        render(<ChoiceHarness initial={fieldNode("ComboboxControl", {})} />);

        const outputTypeField = screen.getByText("Output type").parentElement as HTMLElement;
        expect(within(outputTypeField).getByRole("combobox")).toHaveTextContent("string");

        await user.click(screen.getByRole("switch", { name: "Multiple" }));

        expect(within(outputTypeField).getByRole("combobox")).toHaveTextContent("array");
    });
});
