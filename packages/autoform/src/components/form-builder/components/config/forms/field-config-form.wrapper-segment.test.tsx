/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DesignerProvider } from "../../../hooks/use-designer-context";
import type { UseDesignerReturn } from "../../../hooks/use-form-designer";
import type { DesignerField, DesignerNode } from "../../../types";
import { ConfigPanel } from "../config-panel";

// EventsEditor (now wired into FieldConfigForm, SA-2177) mounts its EventCodeEditor for any
// control with events — real @monaco-editor/react tries to fetch its loader script from a CDN
// and throws under happy-dom. Stub it exactly like events-editor.test.tsx / event-code-editor.test.tsx.
rs.mock("@monaco-editor/react", () => ({
    default: (props: { value: string; onChange?: (v: string) => void }) => (
        <textarea aria-label="code" value={props.value} onChange={(e) => props.onChange?.(e.target.value)} />
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

/**
 * A plain text field. The Wrapper selector (`WrapperSegment`) replaced the old `<Select>`
 * with a `ToggleGroup type="single"` — which Radix renders as a `radiogroup` of `radio`
 * items, so the three options are queried by `role: "radio"`, not `button`.
 */
function textField(overrides: Partial<DesignerField> = {}): DesignerField {
    return {
        _id: "f1",
        _kind: "field",
        key: "email",
        outputType: "string",
        fieldConfig: {
            fieldWrapper: "FormItemWrapper",
            fieldControl: "InputControl",
            wrapperProps: { label: "Email" },
            controlProps: { placeholder: "you@example.com" },
            rules: [],
            condition: null,
        },
        ...overrides,
    } as DesignerField;
}

function renderPanel(node: DesignerNode) {
    const updateNode = rs.fn();
    const ctx = { nodes: [node], selectedNode: node, updateNode } as unknown as UseDesignerReturn;
    const utils = render(
        <DesignerProvider value={ctx}>
            <ConfigPanel />
        </DesignerProvider>
    );
    return { ...utils, updateNode };
}

describe("FieldConfigForm — Wrapper segmented control (SA-2043 P3)", () => {
    it("renders the three wrapper options as segments", async () => {
        const user = userEvent.setup();
        renderPanel(textField());

        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        expect(screen.getByRole("radio", { name: "Form Item" })).toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Div" })).toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Fragment" })).toBeInTheDocument();
    });

    it("marks the current wrapper as the active segment", async () => {
        const user = userEvent.setup();
        renderPanel(textField());

        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        // FormItemWrapper is the field's wrapper → its segment is checked, the others are not.
        expect(screen.getByRole("radio", { name: "Form Item" })).toHaveAttribute("data-state", "on");
        expect(screen.getByRole("radio", { name: "Div" })).toHaveAttribute("data-state", "off");
    });

    it("drives the node's fieldWrapper when a segment is clicked", async () => {
        const user = userEvent.setup();
        const node = textField();
        const { updateNode } = renderPanel(node);

        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        await user.click(screen.getByRole("radio", { name: "Div" }));

        // The segment drives the SAME setFieldWrapper the old <Select> did (via updateNode).
        expect(updateNode).toHaveBeenCalled();
        const lastCall = updateNode.mock.calls.at(-1)!;
        expect(lastCall[0]).toBe("f1");
        const updated = (lastCall[1] as (n: DesignerNode) => DesignerNode)(node);
        expect(updated.fieldConfig.fieldWrapper).toBe("DivWrapper");
    });
});
