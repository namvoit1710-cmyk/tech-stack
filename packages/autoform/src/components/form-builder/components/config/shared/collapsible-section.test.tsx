/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CollapsibleSection } from "./collapsible-section";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

describe("CollapsibleSection", () => {
    it("renders the label and a count badge", () => {
        render(
            <CollapsibleSection label="Validation" count={2}>
                <span>rows</span>
            </CollapsibleSection>
        );
        expect(screen.getByText("Validation")).toBeInTheDocument();
        expect(screen.getByText("2")).toBeInTheDocument();
    });

    it("renders no badge when count is omitted", () => {
        render(<CollapsibleSection label="Condition">content</CollapsibleSection>);
        expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument();
    });

    it("is closed by default and toggles open on click, unmounting content when closed", async () => {
        const user = userEvent.setup();
        render(
            <CollapsibleSection label="Advanced (raw props)">
                <span>hidden content</span>
            </CollapsibleSection>
        );
        expect(screen.queryByText("hidden content")).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: /advanced/i }));
        expect(screen.getByText("hidden content")).toBeInTheDocument();
    });

    it("defaultOpen renders the content immediately", () => {
        render(
            <CollapsibleSection label="Basics" defaultOpen>
                <span>always visible at mount</span>
            </CollapsibleSection>
        );
        expect(screen.getByText("always visible at mount")).toBeInTheDocument();
    });

    it("keepMounted keeps closed content in the DOM (CSS-hidden), not unmounted", () => {
        render(
            <CollapsibleSection label="More settings" keepMounted>
                <span>always in the dom</span>
            </CollapsibleSection>
        );
        expect(screen.getByText("always in the dom")).toBeInTheDocument();
    });
});
