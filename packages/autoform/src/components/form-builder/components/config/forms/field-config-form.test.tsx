/// <reference types="@testing-library/jest-dom" />

import { useState } from "react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DesignerProvider } from "../../../hooks/use-designer-context";
import type { UseDesignerReturn } from "../../../hooks/use-form-designer";
import type { DesignerField, DesignerNode } from "../../../types";
import { FieldConfigForm } from "./field-config-form";

// Monaco is CDN-lazy and never mounts under happy-dom — stub it so the raw grid / advanced
// editor render as a plain textarea.
rs.mock("../../../../field-control/code-control", () => ({
    __esModule: true,
    default: function MockCodeEditor({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
        return <textarea data-testid="code-editor" value={value ?? ""} onChange={(e) => onChange?.(e.target.value)} />;
    },
}));

// ButtonControl's schema has an `icon` widget → DynamicIcon (lazy glyph); stub it deterministically.
rs.mock("lucide-react/dynamic", () => ({
    __esModule: true,
    DynamicIcon: ({ name }: { name: string }) => <span data-testid="dyn-icon" data-name={name} />,
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
afterEach(() => {
    cleanup();
});

function makeField(fieldControl: string, controlProps: Record<string, unknown> = {}, extra: Partial<DesignerField> = {}): DesignerField {
    return {
        _id: "f1",
        _kind: "field",
        key: "field1",
        outputType: fieldControl === "NumberControl" ? "number" : "string",
        fieldConfig: {
            fieldWrapper: "FormItemWrapper",
            fieldControl,
            wrapperProps: { label: "Field 1" },
            controlProps,
            rules: [],
            condition: null,
        },
        ...extra,
    } as DesignerField;
}

/** Controlled harness: updates round-trip so a section that clobbers another shows red. */
function FieldHarness({ initial }: { initial: DesignerField }) {
    const [node, setNode] = useState<DesignerField>(initial);
    const update = ((_id: string, fn: (n: DesignerField) => DesignerField) =>
        setNode((p) => fn(p))) as unknown as UseDesignerReturn["updateNode"];
    const ctx = { nodes: [node], selectedNode: node, updateNode: update } as unknown as UseDesignerReturn;
    return (
        <DesignerProvider value={ctx}>
            <FieldConfigForm node={node} updateNode={update} />
            <pre data-testid="doc">{JSON.stringify(node)}</pre>
        </DesignerProvider>
    );
}

function doc(): DesignerField {
    return JSON.parse(screen.getByTestId("doc").textContent ?? "{}") as DesignerField;
}

describe("FieldConfigForm — ButtonControl is an action component (SA-2040)", () => {
    it("suppresses Output type for a ButtonControl", () => {
        render(<FieldHarness initial={makeField("ButtonControl")} />);
        // The Output-type Select trigger has no wired accessible name, so assert on its label text.
        expect(screen.queryByText("Output type")).not.toBeInTheDocument();
    });

    it("suppresses the Default value input for a ButtonControl", () => {
        render(<FieldHarness initial={makeField("ButtonControl")} />);
        expect(screen.queryByPlaceholderText("default value")).not.toBeInTheDocument();
    });

    it("still shows Output type + Default value for a data control (InputControl)", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("InputControl")} />);
        expect(screen.getByText("Output type")).toBeInTheDocument();
        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        expect(screen.getByPlaceholderText("default value")).toBeInTheDocument();
    });

    it("renders the Button action-DSL widgets from the typed schema", () => {
        render(<FieldHarness initial={makeField("ButtonControl")} />);
        // action-DSL: 'Action' is a text input, 'Confirm before' is a switch.
        expect(screen.getByRole("textbox", { name: "Action" })).toBeInTheDocument();
        expect(screen.getByRole("switch", { name: "Confirm before" })).toBeInTheDocument();
    });
});

describe("FieldConfigForm — field-ref lists siblings (SA-2040)", () => {
    it("a CodeControl's languageField lists sibling field keys, not its own", async () => {
        const user = userEvent.setup();
        const code = makeField("CodeControl", {}, { _id: "body", key: "body" });
        const sibling = makeField("InputControl", {}, { _id: "lang", key: "lang" });
        const ctx = { nodes: [code, sibling], selectedNode: code, updateNode: rs.fn() } as unknown as UseDesignerReturn;
        render(
            <DesignerProvider value={ctx}>
                <FieldConfigForm node={code} updateNode={rs.fn()} />
            </DesignerProvider>
        );

        await user.click(screen.getByRole("combobox", { name: "Language from field" }));

        expect(screen.getByRole("option", { name: "lang" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "body" })).not.toBeInTheDocument();
    });
});

describe("FieldConfigForm — typed Control Props (SA-2014)", () => {
    it("renders typed widgets for InputControl", () => {
        render(<FieldHarness initial={makeField("InputControl")} />);
        expect(screen.getByRole("textbox", { name: "Placeholder" })).toBeInTheDocument();
        expect(screen.getByRole("spinbutton", { name: "Max length" })).toBeInTheDocument();
        expect(screen.getByRole("switch", { name: "Read-only" })).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "CSS class" })).toBeInTheDocument();
    });

    it("renders typed widgets for TextareaControl and never the dead `rows`", () => {
        render(<FieldHarness initial={makeField("TextareaControl")} />);
        expect(screen.getByRole("textbox", { name: "Placeholder" })).toBeInTheDocument();
        expect(screen.queryByRole("spinbutton", { name: /rows/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("textbox", { name: /rows/i })).not.toBeInTheDocument();
    });

    it("renders typed widgets for PasswordControl — placeholder/read-only/css class only, no length", () => {
        render(<FieldHarness initial={makeField("PasswordControl")} />);
        expect(screen.getByRole("textbox", { name: "Placeholder" })).toBeInTheDocument();
        expect(screen.getByRole("switch", { name: "Read-only" })).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "CSS class" })).toBeInTheDocument();
        expect(screen.queryByRole("spinbutton", { name: "Max length" })).not.toBeInTheDocument();
    });

    it("renders typed widgets for NumberControl (min/max spinbuttons, lowercase readonly switch) and never `step`", () => {
        render(<FieldHarness initial={makeField("NumberControl")} />);
        expect(screen.getByRole("spinbutton", { name: "Min value" })).toBeInTheDocument();
        expect(screen.getByRole("spinbutton", { name: "Max value" })).toBeInTheDocument();
        expect(screen.getByRole("switch", { name: "Read-only" })).toBeInTheDocument();
        expect(screen.queryByRole("spinbutton", { name: /step/i })).not.toBeInTheDocument();
    });

    it("does not offer type/pattern on Input — those are validation concerns", () => {
        render(<FieldHarness initial={makeField("InputControl")} />);
        expect(screen.queryByRole("textbox", { name: /^type$/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("textbox", { name: /pattern/i })).not.toBeInTheDocument();
    });

    it("renders the typed Control Props section for CodeControl (SA-2040 typed it)", () => {
        // CodeControl gained a CONTROL_PROP_SCHEMA in SA-2040, so it now shows typed widgets, not
        // the raw key/value grid. Every shipped control is typed now, so ControlPropsBlock's raw
        // grid fallback is reachable only by an unknown/future control.
        render(<FieldHarness initial={makeField("CodeControl")} />);
        expect(screen.getByRole("switch", { name: "Read-only" })).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: "Language from field" })).toBeInTheDocument();
    });

    it("writes className as a string to controlProps (Input)", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("InputControl")} />);
        await user.type(screen.getByRole("textbox", { name: "CSS class" }), "text-right");
        expect(doc().fieldConfig.controlProps?.className).toBe("text-right");
        expect(typeof doc().fieldConfig.controlProps?.className).toBe("string");
    });

    it("writes className as a string to controlProps (Password — panel side of the cn fix)", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("PasswordControl")} />);
        await user.type(screen.getByRole("textbox", { name: "CSS class" }), "font-mono");
        expect(doc().fieldConfig.controlProps?.className).toBe("font-mono");
    });
});

describe("FieldConfigForm — typed default value (SA-2014 R3)", () => {
    it("NumberControl default writes a real number", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("NumberControl")} />);
        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        const input = screen.getByPlaceholderText("default value");
        expect(input).toHaveAttribute("type", "number");
        await user.type(input, "5");
        expect(doc().default).toBe(5);
        expect(typeof doc().default).toBe("number");
    });

    it("clearing a NumberControl default removes it (never 0, never the string '')", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("NumberControl", {}, { default: 7 } as Partial<DesignerField>)} />);
        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        await user.clear(screen.getByPlaceholderText("default value"));
        expect(doc()).not.toHaveProperty("default");
    });

    it("Input default stays a string", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("InputControl")} />);
        // SA-2175: Basics is a collapsible section, closed by default — open it first.
        await user.click(screen.getByRole("button", { name: /basics/i }));
        await user.type(screen.getByPlaceholderText("default value"), "hello");
        expect(doc().default).toBe("hello");
        expect(typeof doc().default).toBe("string");
    });
});

describe("FieldConfigForm — Advanced (raw) escape hatch (SA-2014)", () => {
    it("badges an unknown control prop and keeps it editable + round-tripping under Advanced", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("InputControl", { foo: "bar" })} />);

        // Collapsed: the badge advertises the one hidden key.
        expect(screen.getByTestId("advanced-hidden-count")).toHaveTextContent("1");

        await user.click(screen.getByRole("button", { name: /advanced/i }));
        const valueCell = screen.getByDisplayValue("bar");
        expect(valueCell).toBeInTheDocument();

        await user.type(valueCell, "!");
        expect(doc().fieldConfig.controlProps?.foo).toBe("bar!");
    });

    it("does not badge the schema keys themselves — they are covered by the typed panel", () => {
        render(<FieldHarness initial={makeField("InputControl", { placeholder: "hi", className: "text-right" })} />);
        expect(screen.queryByTestId("advanced-hidden-count")).not.toBeInTheDocument();
    });

    it("keeps a typed edit and a raw edit from clobbering each other", async () => {
        const user = userEvent.setup();
        render(<FieldHarness initial={makeField("InputControl", { foo: "bar" })} />);

        // Typed edit writes placeholder…
        await user.type(screen.getByRole("textbox", { name: "Placeholder" }), "hi");
        // …and the unknown `foo` still survives (open Advanced to see it).
        await user.click(screen.getByRole("button", { name: /advanced/i }));
        expect(screen.getByDisplayValue("bar")).toBeInTheDocument();
        expect(doc().fieldConfig.controlProps?.foo).toBe("bar");
        expect(doc().fieldConfig.controlProps?.placeholder).toBe("hi");
    });
});

// SA-2038 Tasks 3+4 — helpers mirror field-config-form.choice.test.tsx's fieldNode/renderForm.
function fieldNode(fieldControl: string, controlProps: Record<string, unknown> = {}): DesignerNode {
    return {
        _id: "f1",
        _kind: "field",
        key: "when",
        outputType: "string",
        fieldConfig: { fieldWrapper: "FormItemWrapper", controlProps, wrapperProps: {}, rules: [], condition: null, fieldControl },
    } as unknown as DesignerNode;
}

function renderForm(node: DesignerNode, updateNode = rs.fn()) {
    const ctx = { nodes: [node], selectedNode: node, updateNode } as unknown as UseDesignerReturn;
    return render(
        <DesignerProvider value={ctx}>
            <FieldConfigForm node={node} updateNode={updateNode} />
        </DesignerProvider>,
    );
}

describe("FieldConfigForm — typed control panel (SA-2038)", () => {
    it("renders DateTimePicker mode + timeMode as locked Selects, not raw key/value rows", () => {
        renderForm(fieldNode("DateTimePickerControl"));
        expect(screen.getByRole("combobox", { name: "mode" })).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: "timeMode" })).toBeInTheDocument();
    });

    it("renders DateTime min/max bounds as datetime widgets, not text inputs", () => {
        renderForm(fieldNode("DateTimePickerControl"));
        expect(screen.getByRole("group", { name: "Min date-time" })).toBeInTheDocument();
        expect(screen.getByRole("group", { name: "Max date-time" })).toBeInTheDocument();
        expect(screen.queryByRole("textbox", { name: "Min date-time" })).not.toBeInTheDocument();
    });

    it("renders TimePicker mode as a 12h|24h Select and bounds as time widgets", async () => {
        const user = (await import("@testing-library/user-event")).default.setup();
        renderForm(fieldNode("TimePickerControl"));
        await user.click(screen.getByRole("combobox", { name: "Time format" }));
        expect(screen.getByRole("option", { name: "12h" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "24h" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "range" })).not.toBeInTheDocument();
    });
});

describe("FieldConfigForm — mode↔outputType coupling (SA-2038)", () => {
    it("writes outputType=object when DateTime mode is set to range", async () => {
        const user = userEvent.setup();
        const updateNode = rs.fn();
        renderForm(fieldNode("DateTimePickerControl"), updateNode);

        await user.click(screen.getByRole("combobox", { name: "mode" }));
        await user.click(screen.getByRole("option", { name: "range" }));

        // One of the updateNode reducers must produce outputType === "object".
        const producedObject = updateNode.mock.calls.some(([, fn]) => {
            const start = { _id: "f1", _kind: "field", outputType: "string", fieldConfig: { controlProps: {} } };
            try { return (fn as (n: unknown) => { outputType?: string })(start).outputType === "object"; }
            catch { return false; }
        });
        expect(producedObject).toBe(true);
    });

    it("writes outputType=string when DateTime mode is set back to single", async () => {
        const user = userEvent.setup();
        const updateNode = rs.fn();
        renderForm(fieldNode("DateTimePickerControl", { mode: "range" }), updateNode);

        await user.click(screen.getByRole("combobox", { name: "mode" }));
        await user.click(screen.getByRole("option", { name: "single" }));

        const producedString = updateNode.mock.calls.some(([, fn]) => {
            const start = { _id: "f1", _kind: "field", outputType: "object", fieldConfig: { controlProps: {} } };
            try { return (fn as (n: unknown) => { outputType?: string })(start).outputType === "string"; }
            catch { return false; }
        });
        expect(producedString).toBe(true);
    });

    it("does NOT couple TimePicker mode (12h/24h) to outputType", async () => {
        const user = userEvent.setup();
        const updateNode = rs.fn();
        renderForm(fieldNode("TimePickerControl"), updateNode);

        await user.click(screen.getByRole("combobox", { name: "Time format" }));
        await user.click(screen.getByRole("option", { name: "12h" }));

        const touchedOutput = updateNode.mock.calls.some(([, fn]) => {
            const start = { _id: "f1", _kind: "field", outputType: "string", fieldConfig: { controlProps: {} } };
            try { return (fn as (n: unknown) => { outputType?: string })(start).outputType === "object"; }
            catch { return false; }
        });
        expect(touchedOutput).toBe(false);
    });
});
