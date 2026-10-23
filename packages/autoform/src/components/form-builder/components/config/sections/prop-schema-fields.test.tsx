/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PropSchema } from "../../../constants/prop-keys";
import { PropSchemaFields } from "./prop-schema-fields";

// LdcCodeEditor is Monaco: it lazy-loads the editor from a CDN and never mounts a
// text surface under happy-dom. Stub it with a textarea so the `json` branch can be
// asserted on the value PropSchemaFields actually hands the editor.
rs.mock("../../../../field-control/code-control", () => ({
    __esModule: true,
    default: function MockCodeEditor({
        value,
        onChange,
    }: {
        value?: string;
        onChange?: (v: string) => void;
    }) {
        return (
            <textarea
                data-testid="code-editor"
                value={value ?? ""}
                onChange={(e) => onChange?.(e.target.value)}
            />
        );
    },
}));

// DynamicIcon lazy-loads its glyph; stub it so the icon widget's preview is deterministic.
rs.mock("lucide-react/dynamic", () => ({
    __esModule: true,
    DynamicIcon: ({ name }: { name: string }) => <span data-testid="dyn-icon" data-name={name} />,
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

const SCHEMA: PropSchema[] = [
    { key: "isAdd", type: "boolean", label: "Add row", default: true },
    { key: "readonly", type: "boolean", label: "Read-only", default: false },
    { key: "selectionMode", type: "enum", label: "Selection mode", options: ["single", "multiple"] },
];

/** The three PropTypes the original fixture never exercised. */
const SCALAR_SCHEMA: PropSchema[] = [
    { key: "placeholder", type: "string", label: "Placeholder" },
    { key: "rowCount", type: "number", label: "Row count" },
    { key: "data", type: "json", label: "Data" },
];

describe("PropSchemaFields", () => {
    it("renders a boolean prop at its default when the value is absent", () => {
        render(<PropSchemaFields schema={SCHEMA} value={{}} onChange={rs.fn()} />);
        expect(screen.getByRole("switch", { name: "Add row" })).toBeChecked();
        expect(screen.getByRole("switch", { name: "Read-only" })).not.toBeChecked();
    });

    it("writes a real boolean — not the string 'true'", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCHEMA} value={{}} onChange={onChange} />);

        await user.click(screen.getByRole("switch", { name: "Read-only" }));

        expect(onChange).toHaveBeenCalledWith({ readonly: true });
        const written = onChange.mock.calls[0][0].readonly;
        expect(typeof written).toBe("boolean");
    });

    it("renders an enum as a select whose options are exactly the legal values plus 'Not set'", async () => {
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCHEMA} value={{}} onChange={rs.fn()} />);

        await user.click(screen.getByRole("combobox", { name: "Selection mode" }));

        expect(screen.getByRole("option", { name: "Not set" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "single" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "multiple" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "none" })).not.toBeInTheDocument();
    });

    it("selecting 'Not set' deletes the key rather than writing a string", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCHEMA} value={{ selectionMode: "single" }} onChange={onChange} />);

        await user.click(screen.getByRole("combobox", { name: "Selection mode" }));
        await user.click(screen.getByRole("option", { name: "Not set" }));

        expect(onChange).toHaveBeenCalledWith({});
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("selectionMode");
    });

    it("renders a string prop as a text input and writes a string", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCALAR_SCHEMA} value={{}} onChange={onChange} />);

        const input = screen.getByRole("textbox", { name: "Placeholder" });
        expect(input).toHaveAttribute("type", "text");

        await user.type(input, "A");

        expect(onChange).toHaveBeenCalledWith({ placeholder: "A" });
        expect(typeof onChange.mock.calls[0][0].placeholder).toBe("string");
    });

    it("renders a number prop as a number input and writes a real number", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCALAR_SCHEMA} value={{}} onChange={onChange} />);

        const input = screen.getByRole("spinbutton", { name: "Row count" });
        expect(input).toHaveAttribute("type", "number");

        await user.type(input, "5");

        expect(onChange).toHaveBeenCalledWith({ rowCount: 5 });
        expect(typeof onChange.mock.calls[0][0].rowCount).toBe("number");
    });

    it("clearing a number input deletes the key rather than writing 0", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCALAR_SCHEMA} value={{ rowCount: 7 }} onChange={onChange} />);

        await user.clear(screen.getByRole("spinbutton", { name: "Row count" }));

        expect(onChange).toHaveBeenCalledWith({});
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("rowCount");
    });

    it("renders an absent json prop as empty — not as '{}'", () => {
        render(<PropSchemaFields schema={SCALAR_SCHEMA} value={{}} onChange={rs.fn()} />);

        const editor = screen.getByTestId("code-editor");
        expect(editor).toHaveValue("");
        expect(editor).not.toHaveValue("{}");
    });

    it("renders a present json prop as pretty JSON", () => {
        render(
            <PropSchemaFields schema={SCALAR_SCHEMA} value={{ data: [{ id: 1 }] }} onChange={rs.fn()} />
        );

        expect(screen.getByTestId("code-editor")).toHaveValue(JSON.stringify([{ id: 1 }], null, 2));
    });

    it("shows a legacy string 'true' boolean as ON — the runtime reads it truthily", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={SCHEMA} value={{ isAdd: "true" }} onChange={onChange} />);

        const sw = screen.getByRole("switch", { name: "Add row" });
        expect(sw).toBeChecked();

        // Touching it self-heals the legacy string into a real boolean.
        await user.click(sw);

        expect(onChange).toHaveBeenCalledWith({ isAdd: false });
        expect(typeof onChange.mock.calls[0][0].isAdd).toBe("boolean");
    });

    it("shows a stored `false` as OFF even when the schema default is true (?? not ||)", () => {
        render(<PropSchemaFields schema={SCHEMA} value={{ isAdd: false }} onChange={rs.fn()} />);

        expect(screen.getByRole("switch", { name: "Add row" })).not.toBeChecked();
    });

    it("associates every label with its widget", () => {
        render(<PropSchemaFields schema={[...SCHEMA, ...SCALAR_SCHEMA]} value={{}} onChange={rs.fn()} />);

        const pairs: [string, HTMLElement][] = [
            ["Add row", screen.getByRole("switch", { name: "Add row" })],
            ["Selection mode", screen.getByRole("combobox", { name: "Selection mode" })],
            ["Placeholder", screen.getByRole("textbox", { name: "Placeholder" })],
            ["Row count", screen.getByRole("spinbutton", { name: "Row count" })],
        ];

        for (const [text, control] of pairs) {
            const label = screen.getByText(text, { selector: "label" }) as HTMLLabelElement;
            expect(control.id).toBeTruthy();
            expect(label.htmlFor).toBe(control.id);
        }

        // Monaco owns its own textarea, so the json widget is named via its group.
        expect(screen.getByRole("group", { name: "Data" })).toBeInTheDocument();
    });

    it("renders `help` on a non-enum (number) widget", () => {
        const schema: PropSchema[] = [
            {
                key: "maxLength",
                type: "number",
                label: "Max length",
                help: "Hard cap — blocks typing past N with NO message.",
            },
        ];
        render(<PropSchemaFields schema={schema} value={{}} onChange={rs.fn()} />);
        expect(screen.getByText(/Hard cap/)).toBeInTheDocument();
    });

    const OPTIONS_SCHEMA: PropSchema[] = [{ key: "options", type: "options", label: "Options" }];

    it("renders an `options` prop as the OptionsEditor (id/value rows), not a JSON input", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <PropSchemaFields
                schema={OPTIONS_SCHEMA}
                value={{ options: [{ id: "a", value: "Apple" }] }}
                onChange={onChange}
            />
        );
        // OptionsEditor renders an "Add option" affordance + the id/value inputs.
        expect(screen.getByRole("button", { name: /add option/i })).toBeInTheDocument();
        expect(screen.getByDisplayValue("a")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Apple")).toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: /add option/i }));
        expect(onChange).toHaveBeenCalled();
        const written = onChange.mock.calls[0][0].options as unknown[];
        expect(Array.isArray(written)).toBe(true);
        expect(written).toHaveLength(2);
    });

    const LIST_SCHEMA: PropSchema[] = [{ key: "dependencies", type: "list", label: "Dependencies" }];

    it("renders a `list` prop as string rows and appends on add", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={LIST_SCHEMA} value={{ dependencies: ["country"] }} onChange={onChange} />);

        expect(screen.getByRole("textbox", { name: "Dependencies 1" })).toHaveValue("country");

        await user.click(screen.getByRole("button", { name: /add dependencies/i }));
        expect(onChange).toHaveBeenCalledWith({ dependencies: ["country", ""] });
    });

    it("removes a `list` row", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={LIST_SCHEMA} value={{ dependencies: ["a", "b"] }} onChange={onChange} />);

        await user.click(screen.getByRole("button", { name: "Remove Dependencies 1" }));
        expect(onChange).toHaveBeenCalledWith({ dependencies: ["b"] });
    });

    const KV_SCHEMA: PropSchema[] = [{ key: "params", type: "keyValue", label: "Params" }];

    it("renders a `keyValue` prop as key/value rows", () => {
        render(<PropSchemaFields schema={KV_SCHEMA} value={{ params: { region: "EU" } }} onChange={rs.fn()} />);
        expect(screen.getByRole("textbox", { name: "Params key 1" })).toHaveValue("region");
        expect(screen.getByRole("textbox", { name: "Params value 1" })).toHaveValue("EU");
    });

    it("edits a `keyValue` value and writes back the whole map", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={KV_SCHEMA} value={{ params: { region: "E" } }} onChange={onChange} />);

        await user.type(screen.getByRole("textbox", { name: "Params value 1" }), "U");
        expect(onChange).toHaveBeenCalledWith({ params: { region: "EU" } });
    });

    it("adds a `keyValue` row", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={KV_SCHEMA} value={{ params: {} }} onChange={onChange} />);

        await user.click(screen.getByRole("button", { name: /add params/i }));
        expect(onChange).toHaveBeenCalledWith({ params: { "": "" } });
    });

    const OBJECT_SCHEMA: PropSchema[] = [
        {
            key: "responseMapping",
            type: "object",
            label: "Response mapping",
            fields: [
                { key: "dataPath", type: "string", label: "Data path" },
                { key: "totalPath", type: "string", label: "Total path" },
            ],
        },
    ];

    it("renders an `object` prop as a nested group of its `fields`", () => {
        render(
            <PropSchemaFields
                schema={OBJECT_SCHEMA}
                value={{ responseMapping: { dataPath: "data.items" } }}
                onChange={rs.fn()}
            />
        );
        expect(screen.getByRole("textbox", { name: "Data path" })).toHaveValue("data.items");
        expect(screen.getByRole("textbox", { name: "Total path" })).toHaveValue("");
    });

    it("writes a nested `object` change back under its key", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={OBJECT_SCHEMA} value={{ responseMapping: {} }} onChange={onChange} />);

        await user.type(screen.getByRole("textbox", { name: "Total path" }), "x");
        expect(onChange).toHaveBeenCalledWith({ responseMapping: { totalPath: "x" } });
    });
});

describe("PropSchemaFields — date/datetime/time widgets (SA-2038)", () => {
    it("renders a `date` prop as a date picker widget, not a text input", () => {
        const schema: PropSchema[] = [{ key: "minDate", type: "date", label: "Min date" }];
        render(<PropSchemaFields schema={schema} value={{}} onChange={rs.fn()} />);
        // The widget is wrapped in a labelled group; a raw string input would be a textbox.
        expect(screen.getByRole("group", { name: "Min date" })).toBeInTheDocument();
        expect(screen.queryByRole("textbox", { name: "Min date" })).not.toBeInTheDocument();
        // DatePicker single renders its trigger button with the label as placeholder.
        expect(screen.getByText("Min date")).toBeInTheDocument();
    });

    it("renders a `datetime` prop as a datetime picker widget, not a text input", () => {
        const schema: PropSchema[] = [{ key: "minDateTime", type: "datetime", label: "Min date-time" }];
        render(<PropSchemaFields schema={schema} value={{}} onChange={rs.fn()} />);
        expect(screen.getByRole("group", { name: "Min date-time" })).toBeInTheDocument();
        expect(screen.queryByRole("textbox", { name: "Min date-time" })).not.toBeInTheDocument();
    });

    it("renders a `time` prop as a time picker widget (role=group 'Time picker')", () => {
        const schema: PropSchema[] = [{ key: "minTime", type: "time", label: "Min time" }];
        render(<PropSchemaFields schema={schema} value={{}} onChange={rs.fn()} />);
        expect(screen.getByRole("group", { name: "Min time" })).toBeInTheDocument();
        expect(screen.getAllByRole("group", { name: "Time picker" }).length).toBeGreaterThan(0);
        expect(screen.queryByRole("textbox", { name: "Min time" })).not.toBeInTheDocument();
    });

    it("commits the picked date string via onChange for a `datetime` widget", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        const schema: PropSchema[] = [{ key: "minDateTime", type: "datetime", label: "Min date-time" }];
        render(<PropSchemaFields schema={schema} value={{}} onChange={onChange} />);
        // Open the datetime trigger and pick "today" — asserting the widget is wired to onChange.
        await user.click(screen.getByText("Min date-time"));
        const today = screen.getAllByRole("gridcell").find((c) => c.getAttribute("data-day"));
        if (today) {
            // Click the day's interactive <button> — react-day-picker wraps it in a
            // role="gridcell" <td> that carries its own independent `data-day`, so clicking
            // the wrapper itself never reaches the button's onClick (same reason
            // date-picker-control.test.tsx's equivalent click is guarded).
            await user.click(today.querySelector("button") ?? today);
            // SA-2038 R4: the single-mode DateTimePicker now drafts the pick inside its popup
            // and only commits via onChange once Confirm is clicked.
            await user.click(screen.getByRole("button", { name: "Confirm" }));
            expect(onChange).toHaveBeenCalled();
        }
    });
});

describe("PropSchemaFields — field-ref widget (SA-2040)", () => {
    const FIELD_REF_SCHEMA: PropSchema[] = [
        { key: "languageField", type: "field-ref", label: "Language from field" },
    ];

    it("lists exactly the sibling field keys plus 'Not set'", async () => {
        const user = userEvent.setup();
        render(
            <PropSchemaFields schema={FIELD_REF_SCHEMA} value={{}} onChange={rs.fn()} fieldRefOptions={["mode", "payload"]} />
        );

        await user.click(screen.getByRole("combobox", { name: "Language from field" }));

        expect(screen.getByRole("option", { name: "Not set" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "mode" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "payload" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "stranger" })).not.toBeInTheDocument();
    });

    it("writes the chosen sibling key as a string", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <PropSchemaFields schema={FIELD_REF_SCHEMA} value={{}} onChange={onChange} fieldRefOptions={["mode"]} />
        );

        await user.click(screen.getByRole("combobox", { name: "Language from field" }));
        await user.click(screen.getByRole("option", { name: "mode" }));

        expect(onChange).toHaveBeenCalledWith({ languageField: "mode" });
        expect(typeof onChange.mock.calls[0][0].languageField).toBe("string");
    });

    it("selecting 'Not set' deletes the key", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <PropSchemaFields schema={FIELD_REF_SCHEMA} value={{ languageField: "mode" }} onChange={onChange} fieldRefOptions={["mode"]} />
        );

        await user.click(screen.getByRole("combobox", { name: "Language from field" }));
        await user.click(screen.getByRole("option", { name: "Not set" }));

        expect(onChange).toHaveBeenCalledWith({});
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("languageField");
    });
});

describe("PropSchemaFields — icon widget (SA-2040)", () => {
    const ICON_SCHEMA: PropSchema[] = [{ key: "icon", type: "icon", label: "Icon" }];

    it("renders a text input and a live preview for the current icon", () => {
        render(<PropSchemaFields schema={ICON_SCHEMA} value={{ icon: "pencil" }} onChange={rs.fn()} />);

        expect(screen.getByRole("combobox", { name: "Icon" })).toHaveValue("pencil");
        expect(screen.getByTestId("dyn-icon")).toHaveAttribute("data-name", "pencil");
    });

    it("writes the typed lucide name as a string", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={ICON_SCHEMA} value={{}} onChange={onChange} />);

        await user.type(screen.getByRole("combobox", { name: "Icon" }), "x");

        expect(onChange).toHaveBeenCalledWith({ icon: "x" });
        expect(typeof onChange.mock.calls[0][0].icon).toBe("string");
    });

    it("clearing the icon deletes the key", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<PropSchemaFields schema={ICON_SCHEMA} value={{ icon: "star" }} onChange={onChange} />);

        await user.clear(screen.getByRole("combobox", { name: "Icon" }));

        expect(onChange).toHaveBeenCalledWith({});
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("icon");
    });
});
