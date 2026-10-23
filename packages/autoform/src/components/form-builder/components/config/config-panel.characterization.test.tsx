/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DesignerProvider } from "../../hooks/use-designer-context";
import type { UseDesignerReturn } from "../../hooks/use-form-designer";
import type { DesignerField, DesignerNode } from "../../types";
import { ConfigPanel } from "./config-panel";

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

function selectField(overrides: Partial<DesignerField> = {}): DesignerField {
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
            rules: [{ method: "required", message: "Email is required" }],
            condition: null,
        },
        ...overrides,
    } as DesignerField;
}

function renderPanel(node: DesignerNode | null) {
    const updateNode = rs.fn();
    const ctx = { nodes: node ? [node] : [], selectedNode: node, updateNode } as unknown as UseDesignerReturn;
    const utils = render(
        <DesignerProvider value={ctx}>
            <ConfigPanel />
        </DesignerProvider>
    );
    return { ...utils, updateNode };
}

describe("ConfigPanel — characterization (field node)", () => {
    it("shows the field's control name in the header", () => {
        renderPanel(selectField());
        expect(screen.getByText("InputControl")).toBeInTheDocument();
    });

    it("opens with only Live preview expanded — Basics starts closed (SA-2175)", () => {
        renderPanel(selectField());
        expect(screen.getByRole("button", { name: /live preview/i })).toHaveAttribute("aria-expanded", "true");
        expect(screen.getByRole("button", { name: /basics/i })).toHaveAttribute("aria-expanded", "false");
    });

    it("renders Identity: key, output type and default value", async () => {
        const user = userEvent.setup();
        renderPanel(selectField());

        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        expect(screen.getByDisplayValue("email")).toBeInTheDocument();
        expect(screen.getByPlaceholderText("default value")).toBeInTheDocument();
    });

    it("edits Label from Basics — Wrapper Props no longer pins it (SA-2156)", async () => {
        const user = userEvent.setup();
        renderPanel(selectField());

        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        expect(screen.getByLabelText("Label")).toHaveValue("Email");
        // Wrapper Props' own key rows no longer include a pinned "label" row.
        expect(screen.queryAllByPlaceholderText("key")).toHaveLength(0);
    });

    it("lists the field's controlProps", () => {
        renderPanel(selectField());
        expect(screen.getByDisplayValue("you@example.com")).toBeInTheDocument();
    });

    it("renders the existing validation rule and its message — badged, Validation starts closed", async () => {
        const user = userEvent.setup();
        renderPanel(selectField());

        // SA-2156: Validation is a collapsible section, closed by default, badged with the count.
        expect(screen.queryByDisplayValue("Email is required")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /validation/i })).toHaveTextContent("1");

        await user.click(screen.getByRole("button", { name: /validation/i }));
        expect(screen.getByDisplayValue("Email is required")).toBeInTheDocument();
    });

    it("shows the condition watch-field as a typed dropdown, not a free-text key box", async () => {
        const user = userEvent.setup();
        renderPanel(selectField());

        // SA-2156: Condition is a collapsible section, closed by default.
        await user.click(screen.getByRole("button", { name: /condition/i }));

        // The old free-text watch box is gone…
        expect(screen.queryByPlaceholderText("e.g. type")).not.toBeInTheDocument();
        // …and with no sibling fields in this single-field fixture, the editor says so and shows no value box.
        expect(screen.getByText("No sibling fields to watch.")).toBeInTheDocument();
        expect(screen.queryByPlaceholderText("value to compare")).not.toBeInTheDocument();
    });

    it("shows the Options section only for controls that take options", () => {
        renderPanel(selectField());
        expect(screen.queryByText("Options")).not.toBeInTheDocument();

        renderPanel(
            selectField({
                fieldConfig: {
                    fieldWrapper: "FormItemWrapper",
                    fieldControl: "SelectControl",
                    wrapperProps: { label: "Kind" },
                    controlProps: { options: [{ id: "a", value: "A" }] },
                    rules: [],
                    condition: null,
                },
            } as Partial<DesignerField>)
        );
        expect(screen.getByText("Options")).toBeInTheDocument();
    });

    /**
     * SURPRISE — the plan assumed this calls `updateNode`. It does not.
     *
     * "Add rule" is a bare `useFieldArray` append (`config-panel.tsx`, the Validation
     * section): it grows the FORM's local field array and never calls `flushRules`, so
     * the document is untouched until the new row is actually edited. The empty row is
     * therefore panel-local state, not node state — a rule the user adds and never
     * touches is silently dropped when the node is re-selected (the `useEffect` on
     * `node._id` replays the field arrays from the node).
     *
     * The same holds for the "Add" buttons on Wrapper Props / Control Props (`wpAppend` /
     * `cpAppend`, both un-flushed) — and it is deliberate elsewhere in this feature:
     * `raw-props-editor.test.tsx` pins "does not write an empty-string key when a row is
     * added". Whoever moves the Validation section out MUST preserve this: flushing on
     * append would write a `{ method: "required" }` rule the user never asked for.
     */
    it("'Add rule' appends a row to the panel but does not write to the node until it is edited", async () => {
        const user = userEvent.setup();
        const { updateNode } = renderPanel(selectField());

        await user.click(screen.getByRole("button", { name: /validation/i }));
        expect(screen.getAllByPlaceholderText("message (optional)")).toHaveLength(1);

        await user.click(screen.getByText("Add rule"));

        // The row is there…
        expect(screen.getAllByPlaceholderText("message (optional)")).toHaveLength(2);
        // …but the document has not been touched.
        expect(updateNode).not.toHaveBeenCalled();

        // Editing the new row is what flushes it — and it flushes BOTH rules.
        await user.type(screen.getAllByPlaceholderText("message (optional)")[1]!, "!");

        expect(updateNode).toHaveBeenCalled();
    });

    it("shows the empty state when nothing is selected", () => {
        renderPanel(null);
        expect(screen.getByText("Select a node to configure")).toBeInTheDocument();
    });
});
