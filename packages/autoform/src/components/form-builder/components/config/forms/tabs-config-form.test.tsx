/// <reference types="@testing-library/jest-dom" />

import { useState } from "react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { DesignerContainer, DesignerNode } from "../../../types";
import { TabsConfigForm } from "./tabs-config-form";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

function makeNode(wrapperProps: Record<string, unknown> = {}): DesignerContainer {
    return {
        _id: "c1",
        _kind: "container",
        wrapperType: "TabWrapper",
        key: "tabs_1",
        outputType: "object",
        fieldConfig: {
            fieldWrapper: "TabWrapper",
            wrapperProps: { label: "Tabs", variant: "default", orientation: "horizontal", ...wrapperProps },
            controlProps: {},
            rules: [],
            condition: null,
        },
        children: [],
        slots: [
            { _id: "s1", key: "general", label: "General Data", children: [] },
            { _id: "s2", key: "pricing", label: "Pricing", className: "p-6", children: [] },
        ],
    } as unknown as DesignerContainer;
}

/** Plain mocks for the read-only assertions — nothing is written through these. */
function handlers() {
    return {
        updateNode: rs.fn(),
        updateSlotLabel: rs.fn(),
        updateSlotClassName: rs.fn(),
        addSlot: rs.fn(),
        removeSlot: rs.fn(),
    };
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
 * The real ConfigPanel owns the node and feeds it back down. A per-tab label/className is a
 * CONTROLLED input, so it only round-trips against a parent that actually re-renders with the
 * new value — a static `node` prop stalls the input at its initial text and the assertion
 * would read whatever the cursor happened to leave behind, not what was typed.
 */
function renderHarness(initial: DesignerContainer = makeNode()) {
    const spies = {
        updateNode: rs.fn(),
        updateSlotLabel: rs.fn(),
        updateSlotClassName: rs.fn(),
        addSlot: rs.fn(),
        removeSlot: rs.fn(),
    };
    function Harness() {
        const [node, setNode] = useState<DesignerContainer>(initial);
        return (
            <TabsConfigForm
                node={node}
                updateNode={(id, fn) => {
                    spies.updateNode(id, fn);
                    setNode((prev) => fn(prev) as DesignerContainer);
                }}
                updateSlotLabel={(cid, sid, label) => {
                    spies.updateSlotLabel(cid, sid, label);
                    setNode((prev) => ({
                        ...prev,
                        slots: prev.slots?.map((s) => (s._id === sid ? { ...s, label } : s)),
                    }));
                }}
                updateSlotClassName={(cid, sid, className) => {
                    spies.updateSlotClassName(cid, sid, className);
                    setNode((prev) => ({
                        ...prev,
                        slots: prev.slots?.map((s) =>
                            s._id === sid ? { ...s, className: className || undefined } : s
                        ),
                    }));
                }}
                addSlot={(cid) => spies.addSlot(cid)}
                removeSlot={(cid, sid) => spies.removeSlot(cid, sid)}
            />
        );
    }
    render(<Harness />);
    return spies;
}

describe("TabsConfigForm", () => {
    it("renders variant and orientation as selects with only their legal values", async () => {
        const user = userEvent.setup();
        render(<TabsConfigForm node={makeNode()} {...handlers()} />);

        await user.click(screen.getByRole("combobox", { name: "Variant" }));
        expect(screen.getByRole("option", { name: "default" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "line" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "pill" })).not.toBeInTheDocument();
    });

    it("offers orientation as a select of exactly horizontal / vertical", async () => {
        const user = userEvent.setup();
        render(<TabsConfigForm node={makeNode()} {...handlers()} />);

        await user.click(screen.getByRole("combobox", { name: "Orientation" }));
        expect(screen.getByRole("option", { name: "horizontal" })).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "vertical" })).toBeInTheDocument();
        // Closed set: an illegal value must never appear as an option.
        expect(screen.queryByRole("option", { name: "diagonal" })).not.toBeInTheDocument();
    });

    it("round-trips a variant choice to wrapperProps", async () => {
        const { updateNode, calls } = captureUpdate();
        const user = userEvent.setup();
        render(
            <TabsConfigForm
                node={makeNode()}
                updateNode={updateNode}
                updateSlotLabel={rs.fn()}
                updateSlotClassName={rs.fn()}
                addSlot={rs.fn()}
                removeSlot={rs.fn()}
            />
        );

        await user.click(screen.getByRole("combobox", { name: "Variant" }));
        await user.click(screen.getByRole("option", { name: "line" }));

        expect(calls[0]?.fieldConfig.wrapperProps?.variant).toBe("line");
    });

    it("lists every tab and lets the label be edited from the panel", async () => {
        const user = userEvent.setup();
        const spies = renderHarness();

        const labelInput = screen.getByDisplayValue("General Data");
        await user.clear(labelInput);
        await user.type(labelInput, "X");

        expect(spies.updateSlotLabel).toHaveBeenCalledWith("c1", "s1", "X");
    });

    it("lets a tab's className be set — unreachable in the builder before this ticket", async () => {
        const user = userEvent.setup();
        const spies = renderHarness();

        const cls = screen.getByLabelText("className for General Data");
        await user.type(cls, "p");

        expect(spies.updateSlotClassName).toHaveBeenCalledWith("c1", "s1", "p");
    });

    it("shows the tab's stable data key read-only (SA-1053 bug K — no longer derived from the label)", () => {
        render(<TabsConfigForm node={makeNode()} {...handlers()} />);

        const key = screen.getByLabelText("key for General Data");
        expect(key).toHaveValue("general");
        expect(key).toHaveAttribute("readonly");
    });

    it("does not offer className on the container: TabWrapper never reads it", () => {
        render(<TabsConfigForm node={makeNode()} {...handlers()} />);
        expect(screen.queryByRole("textbox", { name: "className" })).not.toBeInTheDocument();
    });
});
