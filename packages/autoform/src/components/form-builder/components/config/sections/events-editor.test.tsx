/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("@monaco-editor/react", () => ({
    default: (props: { value: string; onChange?: (v: string) => void }) => (
        <textarea aria-label="code" value={props.value} onChange={(e) => props.onChange?.(e.target.value)} />
    ),
}));

import { EventsEditor } from "./events-editor";
import type { DesignerNode } from "../../../types";

afterEach(() => cleanup());

const node = (control: string, script?: Record<string, string>, id = "n1"): DesignerNode =>
    ({ _id: id, key: "qty", outputType: "string", fieldConfig: { fieldControl: control, script } } as unknown as DesignerNode);

const openEvents = () => fireEvent.click(screen.getByRole("button", { name: /^Events/ }));

describe("EventsEditor", () => {
    it("renders nothing for a control with no events", () => {
        const { container } = render(<EventsEditor node={node("UnknownControl")} setScript={rs.fn()} />);
        expect(container).toBeEmptyDOMElement();
    });

    it("shows a chip per valid event for the control (InputControl → 4)", () => {
        render(<EventsEditor node={node("InputControl")} setScript={rs.fn()} />);
        openEvents();
        for (const e of ["onChange", "onInput", "onFocus", "onBlur"]) {
            expect(screen.getByRole("radio", { name: new RegExp(e, "i") })).toBeInTheDocument();
        }
        // ButtonControl → only onClick
        cleanup();
        render(<EventsEditor node={node("ButtonControl")} setScript={rs.fn()} />);
        openEvents();
        expect(screen.getByRole("radio", { name: /onClick/i })).toBeInTheDocument();
        expect(screen.queryByRole("radio", { name: /onChange/i })).toBeNull();
    });

    it("loads the selected event's source into the editor and defaults to a coded event", () => {
        render(
            <EventsEditor
                node={node("InputControl", { onFocus: "async function onFocus(ctx){ /*coded*/ }" })}
                setScript={rs.fn()}
            />,
        );
        openEvents();
        // onFocus has code → it is the default selection → its source is in the editor
        expect((screen.getByLabelText("code") as HTMLTextAreaElement).value).toContain("/*coded*/");
    });

    it("re-seeds the selected event when the inspected node changes", () => {
        const { rerender } = render(
            <EventsEditor node={node("InputControl", { onFocus: "async function onFocus(ctx){ /*A*/ }" })} setScript={rs.fn()} />,
        );
        openEvents();
        expect(screen.getByRole("radio", { name: /onFocus/i })).toBeChecked();

        // switch to a different node — a ButtonControl (events = ['onClick']) → selection must re-seed, NOT stay on onFocus
        // (the section's open/closed state persists across rerender — it's already expanded, don't re-toggle it)
        rerender(<EventsEditor node={node("ButtonControl", undefined, "n2")} setScript={rs.fn()} />);
        expect(screen.getByRole("radio", { name: /onClick/i })).toBeChecked();
        expect(screen.queryByRole("radio", { name: /onFocus/i })).toBeNull();
    });
});

describe("EventsEditor — authoring", () => {
    it("seeds a scaffold with a JSDoc-typed ctx when the event has no code", () => {
        render(<EventsEditor node={node("InputControl")} setScript={rs.fn()} />);
        openEvents();
        const ta = screen.getByLabelText("code") as HTMLTextAreaElement;
        expect(ta.value).toContain("@param {ActionScriptCtx} ctx");
        expect(ta.value).toContain("async function onChange(ctx)");
    });

    it("writes edits back via setScript(selectedEvent, source)", () => {
        const setScript = rs.fn();
        render(<EventsEditor node={node("InputControl")} setScript={setScript} />);
        openEvents();
        fireEvent.change(screen.getByLabelText("code"), {
            target: { value: "async function onChange(ctx){ ctx.setValue('x', 1); }" },
        });
        expect(setScript).toHaveBeenCalledWith("onChange", "async function onChange(ctx){ ctx.setValue('x', 1); }");
    });

    it("hint chip inserts a ctx snippet via setScript", () => {
        const setScript = rs.fn();
        render(<EventsEditor node={node("InputControl")} setScript={setScript} />);
        openEvents();
        fireEvent.click(screen.getByRole("button", { name: "ctx.toast()" }));
        const [, source] = setScript.mock.calls[0];
        expect(source).toContain("ctx.toast('message', 'info')");
    });
});
