/// <reference types="@testing-library/jest-dom" />

import { useState } from "react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { DesignerContainer, DesignerNode } from "../../../types";
import { OdataConfigForm } from "./odata-config-form";

// LdcCodeEditor is Monaco: it lazy-loads from a CDN and never mounts a text surface under
// happy-dom. Stub it with a textarea so the `actions` row's JSON can be read and written.
// Everything asserted through this stub is what OdataConfigForm/RawPropsEditor actually
// hand the editor — not what Monaco would render.
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

function makeNode(wrapperProps: Record<string, unknown> = {}): DesignerContainer {
    return {
        _id: "n1",
        _kind: "container",
        wrapperType: "OdataWrapper",
        key: "tblProcess",
        outputType: "array",
        fieldConfig: {
            fieldWrapper: "OdataWrapper",
            wrapperProps: { label: "OData Table", ...wrapperProps },
            controlProps: {},
            rules: [],
            condition: null,
        },
        children: [],
        columns: [],
    } as unknown as DesignerContainer;
}

/** Applies the updater the form hands back, so we can assert on the resulting node. */
function captureUpdate() {
    const calls: DesignerNode[] = [];
    const updateNode = rs.fn((_id: string, fn: (n: DesignerNode) => DesignerNode) => {
        calls.push(fn(makeNode()));
    });
    return { updateNode, calls };
}

/**
 * The real ConfigPanel owns the node and feeds it back down. Only a CONTROLLED parent
 * makes the document round-trip real: against a static `node` prop a section that
 * clobbers the other section's keys still looks green, because the clobbered object is
 * never echoed back. The `doc` node below is the document as it now stands.
 */
function NodeHarness({ initial }: { initial: DesignerContainer }) {
    const [node, setNode] = useState<DesignerContainer>(initial);
    return (
        <>
            <OdataConfigForm
                node={node}
                updateNode={(_id, fn) => setNode((prev) => fn(prev) as DesignerContainer)}
            />
            <pre data-testid="doc">{JSON.stringify(node.fieldConfig.wrapperProps)}</pre>
        </>
    );
}

/** The wrapperProps as they now stand in the harness's document. */
function doc(): Record<string, unknown> {
    return JSON.parse(screen.getByTestId("doc").textContent ?? "{}") as Record<string, unknown>;
}

/**
 * The raw-props key cell is a Base UI `Autocomplete`, which renders TWO inputs holding
 * the same value: the visible combobox and an `aria-hidden` mirror. A bare
 * `getByDisplayValue` matches both and throws — query the visible one.
 */
function keyCell(value: string): HTMLElement | undefined {
    return screen.queryAllByDisplayValue(value).find((el) => el.getAttribute("aria-hidden") !== "true");
}

function keyCells(): HTMLElement[] {
    return screen
        .queryAllByPlaceholderText("key")
        .filter((el) => el.getAttribute("aria-hidden") !== "true");
}

const openAdvanced = (user: ReturnType<typeof userEvent.setup>) =>
    user.click(screen.getByRole("button", { name: /advanced/i }));

describe("OdataConfigForm", () => {
    it("explains the data source instead of asking for an endpoint", () => {
        render(<OdataConfigForm node={makeNode()} updateNode={rs.fn()} />);

        expect(screen.getByText(/__tblProcess_file_id/)).toBeInTheDocument();
        expect(screen.queryByLabelText("endpoint")).not.toBeInTheDocument();
    });

    it("renders the capability flags as switches", () => {
        render(<OdataConfigForm node={makeNode({ isAdd: true, readonly: false })} updateNode={rs.fn()} />);

        expect(screen.getByRole("switch", { name: "Add row" })).toBeChecked();
        expect(screen.getByRole("switch", { name: "Read-only" })).not.toBeChecked();
    });

    it("writes a real boolean when a capability switch is toggled", async () => {
        const { updateNode, calls } = captureUpdate();
        const user = userEvent.setup();
        render(<OdataConfigForm node={makeNode({ isAdd: true })} updateNode={updateNode} />);

        await user.click(screen.getByRole("switch", { name: "Read-only" }));

        expect(calls[0]?.fieldConfig.wrapperProps?.readonly).toBe(true);
        expect(typeof calls[0]?.fieldConfig.wrapperProps?.readonly).toBe("boolean");
    });

    it("round-trips selectionMode", async () => {
        const { updateNode, calls } = captureUpdate();
        const user = userEvent.setup();
        render(<OdataConfigForm node={makeNode()} updateNode={updateNode} />);

        await user.click(screen.getByRole("combobox", { name: "Selection mode" }));
        await user.click(screen.getByRole("option", { name: "multiple" }));

        expect(calls[0]?.fieldConfig.wrapperProps?.selectionMode).toBe("multiple");
    });

    it("keeps actions editable under Advanced, even though it is not in the typed schema", async () => {
        const user = userEvent.setup();
        render(
            <OdataConfigForm
                node={makeNode({ actions: [{ action: "validate_table_value" }] })}
                updateNode={rs.fn()}
            />
        );

        await openAdvanced(user);

        expect(keyCell("actions")).toBeInTheDocument();
        expect(screen.getByTestId("code-editor")).toHaveValue(
            JSON.stringify([{ action: "validate_table_value" }], null, 2)
        );
    });

    it("collapses Advanced by default — the typed sections are the front door", () => {
        render(<OdataConfigForm node={makeNode({ actions: [] })} updateNode={rs.fn()} />);

        expect(keyCell("actions")).toBeUndefined();
        expect(screen.queryByTestId("raw-props-count")).not.toBeInTheDocument();
    });

    it("does not offer the dead props anywhere in the curated sections", () => {
        render(<OdataConfigForm node={makeNode()} updateNode={rs.fn()} />);

        expect(screen.queryByRole("switch", { name: /row more action/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("switch", { name: /^Data$/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("switch", { name: /row count/i })).not.toBeInTheDocument();
    });

    it("hides the typed keys from Advanced — nothing is edited in two places", async () => {
        const user = userEvent.setup();
        render(
            <OdataConfigForm
                node={makeNode({ isAdd: true, selectionMode: "single", endpoint: "odata/x/data" })}
                updateNode={rs.fn()}
            />
        );

        await openAdvanced(user);

        expect(keyCell("isAdd")).toBeUndefined();
        expect(keyCell("selectionMode")).toBeUndefined();
        // …but a legacy prop the schema dropped is still there, still editable.
        expect(keyCell("endpoint")).toBeInTheDocument();
    });

    // ── `label` is the canvas display name, and it must stay editable ──────────
    it("edits `label` from the Table section and round-trips it to wrapperProps", async () => {
        const user = userEvent.setup();
        render(<NodeHarness initial={makeNode({ isAdd: true })} />);

        const input = screen.getByLabelText("Label");
        expect(input).toHaveValue("OData Table");

        await user.clear(input);
        await user.type(input, "Process rows");

        // `container-node.tsx:75` renders `wrapperProps?.label ?? container.key` — this IS
        // the node's name on the canvas, and the Table section is the only place it lives.
        expect(doc()).toEqual({ label: "Process rows", isAdd: true });
    });

    it("keeps `label` out of Advanced — the Table section is its one editor", async () => {
        const user = userEvent.setup();
        render(<OdataConfigForm node={makeNode({ endpoint: "odata/x/data" })} updateNode={rs.fn()} />);

        await openAdvanced(user);

        expect(keyCell("label")).toBeUndefined();
        expect(screen.getByLabelText("Label")).toHaveValue("OData Table");
    });

    it("clearing `label` drops the key so the canvas falls back to the node key", async () => {
        const user = userEvent.setup();
        render(<NodeHarness initial={makeNode()} />);

        await user.clear(screen.getByLabelText("Label"));

        // An empty STRING would survive `?? container.key` and render a nameless node.
        expect(doc()).not.toHaveProperty("label");
    });

    // ── the collapsed Advanced header must advertise what it is hiding ─────────
    it("shows how many raw props are hidden — without expanding Advanced", () => {
        render(
            <OdataConfigForm
                node={makeNode({ endpoint: "odata/x/data", isShowRowMoreAction: true })}
                updateNode={rs.fn()}
            />
        );

        // A legacy document's dead props are otherwise invisible: the badge is the only
        // signal that the escape hatch has anything in it.
        expect(screen.getByTestId("advanced-hidden-count")).toHaveTextContent("2");
        expect(screen.queryByTestId("raw-props-count")).not.toBeInTheDocument();
    });

    it("shows no badge when the typed sections cover everything", () => {
        render(<OdataConfigForm node={makeNode({ isAdd: true })} updateNode={rs.fn()} />);

        expect(screen.queryByTestId("advanced-hidden-count")).not.toBeInTheDocument();
    });

    // ── the `actions` DSL is pinned to json ────────────────────────────────────
    it("pins `actions` to json: no type Select to corrupt it with", async () => {
        const user = userEvent.setup();
        render(
            <OdataConfigForm
                node={makeNode({ actions: [{ action: "validate_table_value" }] })}
                updateNode={rs.fn()}
            />
        );

        await openAdvanced(user);

        // The original grid renders a static "json" label for an autoJson key instead of a
        // changeable Select — flipping `actions` to `string` would corrupt the one prop
        // production OData tables genuinely depend on.
        expect(screen.queryByRole("combobox", { name: "Type" })).not.toBeInTheDocument();
        expect(screen.getByTestId("raw-prop-type-actions")).toHaveTextContent("json");
        expect(screen.getByTestId("code-editor")).toBeInTheDocument();
    });

    it("switches a row to json the moment it is renamed INTO `actions`", async () => {
        const user = userEvent.setup();
        render(<NodeHarness initial={makeNode()} />);

        await openAdvanced(user);
        await user.click(screen.getByRole("button", { name: "Add" }));
        await user.type(keyCells()[0]!, "actions");

        expect(screen.queryByRole("combobox", { name: "Type" })).not.toBeInTheDocument();
        expect(screen.getByTestId("raw-prop-type-actions")).toHaveTextContent("json");
        expect(screen.getByTestId("code-editor")).toBeInTheDocument();
    });

    it("still offers a type Select for an ordinary raw prop", async () => {
        const user = userEvent.setup();
        render(<OdataConfigForm node={makeNode({ endpoint: "odata/x/data" })} updateNode={rs.fn()} />);

        await openAdvanced(user);

        expect(screen.getByRole("combobox", { name: "Type" })).toBeInTheDocument();
    });

    // ── the sections must not eat each other ───────────────────────────────────
    it("toggling a capability keeps the raw props (and `label`) intact", async () => {
        const user = userEvent.setup();
        render(
            <NodeHarness
                initial={makeNode({ isAdd: true, actions: [{ action: "validate_table_value" }] })}
            />
        );

        await openAdvanced(user);
        await user.click(screen.getByRole("switch", { name: "Read-only" }));

        expect(doc()).toEqual({
            label: "OData Table",
            isAdd: true,
            actions: [{ action: "validate_table_value" }],
            readonly: true,
        });
        expect(keyCell("actions")).toBeInTheDocument();
    });

    it("editing a raw prop keeps the capability flags (and `label`) intact", async () => {
        const user = userEvent.setup();
        render(
            <NodeHarness initial={makeNode({ isAdd: true, readonly: true, endpoint: "odata/x/data" })} />
        );

        await openAdvanced(user);
        await user.type(screen.getByPlaceholderText("value"), "!");

        expect(doc()).toEqual({
            label: "OData Table",
            isAdd: true,
            readonly: true,
            endpoint: "odata/x/data!",
        });
        expect(screen.getByRole("switch", { name: "Read-only" })).toBeChecked();
        expect(screen.getByRole("switch", { name: "Add row" })).toBeChecked();
    });

    it("selecting 'Not set' deletes selectionMode without touching the raw props", async () => {
        const user = userEvent.setup();
        render(
            <NodeHarness initial={makeNode({ selectionMode: "single", actions: [] })} />
        );

        await openAdvanced(user);
        await user.click(screen.getByRole("combobox", { name: "Selection mode" }));
        await user.click(screen.getByRole("option", { name: "Not set" }));

        expect(doc()).toEqual({ label: "OData Table", actions: [] });
        expect(doc()).not.toHaveProperty("selectionMode");
    });

    it("renames the node's key and re-points the data-source note at it", async () => {
        const user = userEvent.setup();
        render(<NodeHarness initial={makeNode()} />);

        await user.type(screen.getByLabelText("Key"), "X");

        expect(screen.getByText(/__tblProcessX_file_id/)).toBeInTheDocument();
    });
});
