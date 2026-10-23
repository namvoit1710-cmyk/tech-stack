/// <reference types="@testing-library/jest-dom" />
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { ExpressionAutocompleteInput, getActiveToken } from "./expression-autocomplete-input";

afterEach(() => cleanup());

describe("getActiveToken", () => {
    it("returns the token when the caret is inside an unclosed {{", () => {
        expect(getActiveToken("a {{cu", 6)).toEqual({ start: 2, query: "cu" });
    });
    it("strips a leading $ from the query", () => {
        expect(getActiveToken("{{$qu", 5)).toEqual({ start: 0, query: "qu" });
    });
    it("returns null once the token is closed", () => {
        expect(getActiveToken("{{x}} ", 6)).toBeNull();
    });
    it("returns null when there is no {{ before the caret", () => {
        expect(getActiveToken("hello", 5)).toBeNull();
    });
});

const SUGGESTIONS: IExprSuggestion[] = [
    { value: "{{$custom.query}}", label: "query", description: "Text", group: "Custom inputs" },
    { value: "{{$input.", label: "$input.<field>", description: "Workflow input data", group: "Workflow input" },
];

// Stateful wrapper — the component is controlled, so a real consumer owns the value.
const Harness = () => {
    const [v, setV] = useState("");
    return (
        <>
            <ExpressionAutocompleteInput value={v} onChange={setV} suggestions={SUGGESTIONS} />
            <output data-testid="val">{v}</output>
        </>
    );
};

// Type into a React-controlled input. Assigning `input.value` directly updates
// React's internal value tracker, so a plain change event is then treated as a
// no-op and onChange never fires. Setting the value through the *native* prototype
// setter bypasses that tracker, and placing the caret before dispatching lets the
// component read the right selectionStart for token detection.
const typeInto = (input: HTMLInputElement, value: string, caret = value.length) => {
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    nativeSetter.call(input, value);
    input.setSelectionRange(caret, caret);
    fireEvent.input(input);
};

describe("ExpressionAutocompleteInput", () => {
    it("opens a grouped dropdown on {{ and inserts the chosen suggestion", () => {
        render(<Harness />);
        const input = screen.getByRole("textbox") as HTMLInputElement;

        typeInto(input, "{{");

        expect(screen.getByText("Custom inputs")).toBeInTheDocument();
        expect(screen.getByText("Workflow input")).toBeInTheDocument();

        // options insert on mousedown (preserves focus)
        fireEvent.mouseDown(screen.getByText("{{$custom.query}}"));

        expect(screen.getByTestId("val").textContent).toContain("{{$custom.query}}");
    });

    it("filters suggestions by the typed query", () => {
        render(<Harness />);
        const input = screen.getByRole("textbox") as HTMLInputElement;

        typeInto(input, "{{cust");

        expect(screen.getByText("Custom inputs")).toBeInTheDocument();
        expect(screen.queryByText("Workflow input")).not.toBeInTheDocument();
    });
});
