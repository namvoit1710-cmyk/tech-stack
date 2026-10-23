/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TypedDefaultField } from "./typed-default-field";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

describe("TypedDefaultField", () => {
    it("Checkbox/Switch → a real boolean Switch", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<TypedDefaultField controlKey="CheckBoxControl" controlProps={{}} value={false} onChange={onChange} />);
        const sw = screen.getByRole("switch");
        await user.click(sw);
        expect(onChange).toHaveBeenCalledWith(true);
        expect(typeof onChange.mock.calls[0][0]).toBe("boolean");
    });

    it("Select with static options → a picker over the options (no free text)", () => {
        render(
            <TypedDefaultField
                controlKey="SelectControl"
                controlProps={{ options: [{ id: "a", value: "Apple" }, { id: "b", value: "Banana" }] }}
                value="a"
                onChange={rs.fn()}
            />
        );
        // A Select trigger, not a raw text input.
        expect(screen.getByRole("combobox")).toBeInTheDocument();
        expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    });

    it("Combobox with multiple → a multi-pick over the options (checkboxes)", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(
            <TypedDefaultField
                controlKey="ComboboxControl"
                controlProps={{ multiple: true, options: [{ id: "a", value: "Apple" }, { id: "b", value: "Banana" }] }}
                value={["a"]}
                onChange={onChange}
            />
        );
        await user.click(screen.getByRole("checkbox", { name: "Banana" }));
        expect(onChange).toHaveBeenCalledWith(["a", "b"]);
    });

    it("server source → a free-text id + a note (options are server-fetched)", () => {
        render(
            <TypedDefaultField
                controlKey="SelectControl"
                controlProps={{ serverOptions: { endpoint: "x", service: "gateway" } }}
                value=""
                onChange={rs.fn()}
            />
        );
        expect(screen.getByRole("textbox")).toBeInTheDocument();
        expect(screen.getByText(/loaded from the server/i)).toBeInTheDocument();
    });

    it("DatePickerControl → a single date picker, not a text input", () => {
        render(<TypedDefaultField controlKey="DatePickerControl" controlProps={{}} value={undefined} onChange={rs.fn()} />);
        expect(screen.getByText("Default date")).toBeInTheDocument();
        expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    });

    it("DatePickerControl with mode=range → a range date picker", () => {
        render(<TypedDefaultField controlKey="DatePickerControl" controlProps={{ mode: "range" }} value={undefined} onChange={rs.fn()} />);
        expect(screen.getByText("Default range")).toBeInTheDocument();
    });

    it("DateTimePickerControl → a datetime picker", () => {
        render(<TypedDefaultField controlKey="DateTimePickerControl" controlProps={{}} value={undefined} onChange={rs.fn()} />);
        expect(screen.getByText("Default date-time")).toBeInTheDocument();
    });

    it("DateTimePickerControl with mode=range → a range datetime picker", () => {
        render(<TypedDefaultField controlKey="DateTimePickerControl" controlProps={{ mode: "range" }} value={undefined} onChange={rs.fn()} />);
        expect(screen.getByText("Default range")).toBeInTheDocument();
    });

    it("TimePickerControl → a time picker", () => {
        render(<TypedDefaultField controlKey="TimePickerControl" controlProps={{}} value={undefined} onChange={rs.fn()} />);
        expect(screen.getByRole("group", { name: "Time picker" })).toBeInTheDocument();
    });
});
