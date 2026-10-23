/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DesignerProvider } from "../hooks/use-designer-context";
import type { UseDesignerReturn } from "../hooks/use-form-designer";
import { BuilderToolbar } from "./builder-toolbar";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

function renderToolbar(overrides: Partial<{
    view: "builder" | "preview";
    setView: (v: "builder" | "preview") => void;
    clearAll: () => void;
    nodeCount: number;
}> = {}) {
    const setView = overrides.setView ?? rs.fn();
    const clearAll = overrides.clearAll ?? rs.fn();
    const nodeCount = overrides.nodeCount ?? 0;
    const view = overrides.view ?? "builder";

    const ctx = {
        nodes: Array.from({ length: nodeCount }, (_, i) => ({ _id: `n${i}` })),
        clearAll,
    } as unknown as UseDesignerReturn;

    render(
        <DesignerProvider value={ctx}>
            <BuilderToolbar view={view} setView={setView} />
        </DesignerProvider>,
    );

    return { setView, clearAll };
}

describe("BuilderToolbar", () => {
    it("renders the view toggle and Clear all, and NO Add control button", () => {
        renderToolbar({ nodeCount: 1 });

        // The header "+" (Add control) button was removed — the palette opens via
        // right-click / command palette instead.
        expect(screen.queryByRole("button", { name: "Add control" })).not.toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Builder" })).toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Preview" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Clear all" })).toBeInTheDocument();
    });

    it("hides Clear all in preview view but keeps the toggle", () => {
        renderToolbar({ view: "preview", nodeCount: 1 });

        expect(screen.queryByRole("button", { name: "Clear all" })).not.toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Builder" })).toBeInTheDocument();
        expect(screen.getByRole("radio", { name: "Preview" })).toBeInTheDocument();
    });

    it("hides Clear all when there are no nodes", () => {
        renderToolbar({ nodeCount: 0 });
        expect(screen.queryByRole("button", { name: "Clear all" })).not.toBeInTheDocument();
    });

    it("shows Clear all when there are nodes, and clicking it calls clearAll", async () => {
        const user = userEvent.setup();
        const { clearAll } = renderToolbar({ nodeCount: 2 });

        await user.click(screen.getByRole("button", { name: "Clear all" }));
        expect(clearAll).toHaveBeenCalledTimes(1);
    });

    it("calls setView when the toggle is clicked", async () => {
        const user = userEvent.setup();
        const { setView } = renderToolbar({ view: "builder" });

        await user.click(screen.getByRole("radio", { name: "Preview" }));
        expect(setView).toHaveBeenCalledWith("preview");
    });
});
