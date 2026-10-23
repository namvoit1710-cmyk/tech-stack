/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { DesignerProvider } from "../hooks/use-designer-context";
import type { UseDesignerReturn } from "../hooks/use-form-designer";
import type { DesignerField } from "../types";
import { PreviewCanvas } from "./preview-canvas";

afterEach(() => {
    cleanup();
});

function inputField(): DesignerField {
    return {
        _id: "f1",
        _kind: "field",
        key: "email",
        outputType: "string",
        fieldConfig: {
            fieldWrapper: "FormItemWrapper",
            fieldControl: "InputControl",
            wrapperProps: { label: "Email" },
            controlProps: { placeholder: "you@example.com" },
            rules: [],
            condition: null,
        },
    } as DesignerField;
}

function renderWithNodes(nodes: DesignerField[]) {
    const ctx = { nodes } as unknown as UseDesignerReturn;
    return render(
        <DesignerProvider value={ctx}>
            <PreviewCanvas />
        </DesignerProvider>
    );
}

describe("PreviewCanvas — view-only preview (SA-2159)", () => {
    it("renders no Submit, Reset, Values, or header reset-icon buttons", () => {
        renderWithNodes([inputField()]);

        expect(screen.queryByText("Submit")).not.toBeInTheDocument();
        expect(screen.queryByText("Reset")).not.toBeInTheDocument();
        expect(screen.queryByText("Values")).not.toBeInTheDocument();
        expect(screen.queryByTitle("Reset form")).not.toBeInTheDocument();
    });

    it("renders the form on a card surface instead of bare grey", () => {
        renderWithNodes([inputField()]);

        const surface = screen.getByPlaceholderText("you@example.com").closest("div.rounded-lg");
        expect(surface).not.toBeNull();
        expect(surface).toHaveClass("bg-card");
        expect(surface).toHaveClass("border");
    });

    it("does not use the ugly gray background wrapper (SA-2175)", () => {
        const { container } = renderWithNodes([inputField()]);

        expect(container.firstChild).not.toHaveClass("bg-secondary");
    });

    it("still shows the empty state when there are no fields", () => {
        renderWithNodes([]);

        expect(screen.getByText("No fields yet")).toBeInTheDocument();
    });
});
