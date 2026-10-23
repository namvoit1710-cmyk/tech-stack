/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { isSingleExpression, OptionsVariableInput } from "./options-variable-input";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

const dropText = (el: HTMLElement, text: string) =>
    fireEvent.drop(el, { dataTransfer: { getData: (type: string) => (type === "text/plain" ? text : "") } });

describe("isSingleExpression", () => {
    it.each([
        ["{{$http_1.response_body.items}}", true],
        ["{{$node-a.data[0].list}}", true],
        [" {{$n.items}} ", true],
        ["{{ $n.items }}", false],
        ["prefix {{$n.items}}", false],
        ["{{$a.x}}{{$b.y}}", false],
        ["plain", false],
    ])("%s → %s", (input, expected) => {
        expect(isSingleExpression(input)).toBe(expected);
    });
});

describe("OptionsVariableInput", () => {
    it("replaces the whole value with the dropped expression", () => {
        const onChange = rs.fn();
        render(<OptionsVariableInput value="{{$old.value}}" onChange={onChange} />);
        dropText(screen.getByRole("textbox", { name: "Options variable" }), " {{$http_1.response_body.items}} ");
        expect(onChange).toHaveBeenCalledWith("{{$http_1.response_body.items}}");
    });

    it("ignores a drop with no text", () => {
        const onChange = rs.fn();
        render(<OptionsVariableInput value="" onChange={onChange} />);
        dropText(screen.getByRole("textbox", { name: "Options variable" }), "");
        expect(onChange).not.toHaveBeenCalled();
    });

    it("forwards typed input", () => {
        const onChange = rs.fn();
        render(<OptionsVariableInput value="" onChange={onChange} />);
        fireEvent.change(screen.getByRole("textbox", { name: "Options variable" }), { target: { value: "{{$n.items}}" } });
        expect(onChange).toHaveBeenCalledWith("{{$n.items}}");
    });

    it("warns on a malformed expression", () => {
        render(<OptionsVariableInput value="{{ $n.items }}" onChange={rs.fn()} />);
        expect(screen.getByRole("alert")).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "Options variable" })).toHaveAttribute("aria-invalid", "true");
    });

    it("does not warn on a valid or empty value", () => {
        const { rerender } = render(<OptionsVariableInput value="{{$n.items}}" onChange={rs.fn()} />);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        rerender(<OptionsVariableInput value="" onChange={rs.fn()} />);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
});
