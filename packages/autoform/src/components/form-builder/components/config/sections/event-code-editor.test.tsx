/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";

const addExtraLib = rs.fn();
const setDiagnosticsOptions = rs.fn();

// Mock the Monaco react wrapper: render a textarea, and invoke beforeMount with a
// fake `monaco` so we can assert the ctx extraLib + diagnostics are registered.
rs.mock("@monaco-editor/react", () => ({
    default: (props: { value: string; onChange?: (v: string) => void; beforeMount?: (m: unknown) => void }) => {
        props.beforeMount?.({
            languages: { typescript: { javascriptDefaults: { addExtraLib, setDiagnosticsOptions }, ScriptTarget: { ESNext: 99 } } },
        });
        return (
            <textarea
                aria-label="code"
                value={props.value}
                onChange={(e) => props.onChange?.(e.target.value)}
            />
        );
    },
}));

import { EventCodeEditor } from "./event-code-editor";

afterEach(() => cleanup());

describe("EventCodeEditor", () => {
    it("renders the value and propagates edits", () => {
        const onChange = rs.fn();
        render(<EventCodeEditor value="async function onChange(ctx){}" onChange={onChange} />);
        const ta = screen.getByLabelText("code") as HTMLTextAreaElement;
        expect(ta.value).toBe("async function onChange(ctx){}");
        fireEvent.change(ta, { target: { value: "async function onChange(ctx){ ctx.toast('hi'); }" } });
        expect(onChange).toHaveBeenCalledWith("async function onChange(ctx){ ctx.toast('hi'); }");
    });

    it("registers the ctx extraLib on mount", () => {
        render(<EventCodeEditor value="" onChange={rs.fn()} />);
        expect(addExtraLib).toHaveBeenCalled();
        const [dts] = addExtraLib.mock.calls[0];
        expect(dts).toContain("interface ActionScriptCtx");
        expect(dts).toContain("declare const ctx");
    });
});
