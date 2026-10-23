/// <reference types="@testing-library/jest-dom" />

import { useState } from "react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RawPropsEditor } from "./raw-props-editor";

// LdcCodeEditor is Monaco: it lazy-loads the editor from a CDN and never mounts a text
// surface under happy-dom. Stub it with a textarea so the `json` row's value can be
// asserted on what RawPropsEditor actually hands the editor.
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

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

/**
 * The key cell is a Base UI `Autocomplete`, which renders TWO inputs holding the same
 * value: the visible combobox and an `aria-hidden` mirror input for form submission.
 * A bare `getByDisplayValue` therefore matches twice and throws — query the visible one.
 */
function keyCell(value: string): HTMLElement | undefined {
    return screen
        .queryAllByDisplayValue(value)
        .find((el) => el.getAttribute("aria-hidden") !== "true");
}

/** Every visible key input, in row order (the aria-hidden mirrors filtered out). */
function keyCells(): HTMLElement[] {
    return screen
        .queryAllByPlaceholderText("key")
        .filter((el) => el.getAttribute("aria-hidden") !== "true");
}

/**
 * The Config Panel owns `wrapperProps` and feeds it back down, so the editor is only
 * honest under a CONTROLLED parent: bugs that need a round-trip through the document
 * (a rewritten React key remounting the row, a value the parent echoes back with a
 * different inferred type) are invisible against a static `value` prop.
 */
function Harness({
    initial,
    coveredKeys = [],
    onChange,
    nodeId = "node-1",
}: {
    initial: Record<string, unknown>;
    coveredKeys?: string[];
    onChange?: (next: Record<string, unknown>) => void;
    nodeId?: string;
}) {
    const [value, setValue] = useState<Record<string, unknown>>(initial);
    return (
        <RawPropsEditor
            nodeId={nodeId}
            value={value}
            coveredKeys={coveredKeys}
            suggestions={[]}
            onChange={(next) => {
                onChange?.(next);
                setValue(next);
            }}
        />
    );
}

describe("RawPropsEditor", () => {
    it("wraps its rows in the shared horizontal padding gutter, not flush to the panel edge (SA-2175)", () => {
        render(
            <RawPropsEditor
                nodeId="node-1"
                value={{ isAdd: true }}
                coveredKeys={[]}
                suggestions={[]}
                onChange={rs.fn()}
            />
        );

        expect(screen.getByTestId("raw-props-count").closest("p")?.parentElement).toHaveClass("px-3");
    });

    it("shows only the keys the typed schema does not cover", () => {
        render(
            <RawPropsEditor
                nodeId="node-1"
                value={{ isAdd: true, actions: [{ action: "validate_table_value" }] }}
                coveredKeys={["isAdd", "isEdit"]}
                suggestions={[]}
                onChange={rs.fn()}
            />
        );

        expect(keyCell("actions")).toBeInTheDocument();
        expect(keyCell("isAdd")).toBeUndefined();
    });

    it("keeps the `actions` DSL fully editable as pretty JSON", () => {
        render(
            <RawPropsEditor
                nodeId="node-1"
                value={{ actions: [{ action: "validate_table_value" }] }}
                coveredKeys={[]}
                suggestions={[]}
                onChange={rs.fn()}
            />
        );

        expect(screen.getByTestId("code-editor")).toHaveValue(
            JSON.stringify([{ action: "validate_table_value" }], null, 2)
        );
    });

    it("keeps a legacy prop visible so removing it from the schema never hides it from the document", () => {
        render(
            <RawPropsEditor
                nodeId="node-1"
                value={{ isShowRowMoreAction: true, endpoint: "odata/x/data" }}
                coveredKeys={["isAdd"]}
                suggestions={[]}
                onChange={rs.fn()}
            />
        );

        expect(keyCell("isShowRowMoreAction")).toBeInTheDocument();
        expect(keyCell("endpoint")).toBeInTheDocument();
    });

    it("reports how many uncovered keys it is holding", () => {
        render(
            <RawPropsEditor
                nodeId="node-1"
                value={{ actions: [], endpoint: "" }}
                coveredKeys={[]}
                suggestions={[]}
                onChange={rs.fn()}
            />
        );
        expect(screen.getByTestId("raw-props-count")).toHaveTextContent("2");
    });

    // ── D1: the row must survive a rename ──────────────────────────────────────
    it("keeps focus while a multi-character key is typed", async () => {
        const user = userEvent.setup();
        render(<Harness initial={{ a: "1" }} />);

        const cell = keyCells()[0];
        await user.click(cell);
        await user.keyboard("bcd");

        // Keying the row by the PROP KEY remounts it on every keystroke: the input is
        // torn down, focus lands on <body>, and every character after the first is lost.
        expect(keyCells()[0]).toHaveValue("abcd");
        expect(keyCells()[0]).toHaveFocus();
    });

    // ── D2: the type is the user's choice, not a guess about the value ────────
    it("keeps a json row on `json` while its content is momentarily invalid JSON", () => {
        render(<Harness initial={{ actions: [{ action: "validate_table_value" }] }} />);

        // Mid-edit: the user has deleted back to an unfinished array literal.
        fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "[{" } });

        // Re-inferring the type from the value flips the Select to `string` and unmounts
        // Monaco under the user's cursor.
        expect(screen.getByRole("combobox", { name: "Type" })).toHaveTextContent("json");
        expect(screen.getByTestId("code-editor")).toBeInTheDocument();
        expect(screen.getByTestId("code-editor")).toHaveValue("[{");
    });

    // ── D3: a blank row is a UI affordance, not a document write ──────────────
    it("does not write an empty-string key when a row is added", async () => {
        const onChange = rs.fn();
        const user = userEvent.setup();
        render(<Harness initial={{}} onChange={onChange} />);

        await user.click(screen.getByRole("button", { name: "Add" }));

        expect(onChange).not.toHaveBeenCalled();
        expect(keyCells()).toHaveLength(1);

        await user.type(keyCells()[0], "endpoint");

        expect(onChange).toHaveBeenLastCalledWith({ endpoint: "" });
        for (const [written] of onChange.mock.calls) {
            expect(Object.keys(written as object)).not.toContain("");
        }
    });
});
