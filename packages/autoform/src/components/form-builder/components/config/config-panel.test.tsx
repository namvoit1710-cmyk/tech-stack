/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { DesignerProvider } from "../../hooks/use-designer-context";
import type { UseDesignerReturn } from "../../hooks/use-form-designer";
import type { DesignerContainer } from "../../types";
import { ConfigPanel } from "./config-panel";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

const odataNode: DesignerContainer = {
    _id: "n1",
    _kind: "container",
    wrapperType: "OdataWrapper",
    key: "tblProcess",
    outputType: "array",
    fieldConfig: {
        fieldWrapper: "OdataWrapper",
        wrapperProps: { label: "OData Table", isAdd: true },
        controlProps: {},
        rules: [],
        condition: null,
    },
    children: [],
    columns: [],
} as unknown as DesignerContainer;

function renderWithCtx(selectedNode: DesignerContainer | null) {
    const ctx = {
        nodes: selectedNode ? [selectedNode] : [],
        selectedNode,
        updateNode: rs.fn(),
    } as unknown as UseDesignerReturn;

    return render(
        <DesignerProvider value={ctx}>
            <ConfigPanel />
        </DesignerProvider>
    );
}

describe("ConfigPanel dispatch", () => {
    it("routes an OdataWrapper to the typed OData form", () => {
        renderWithCtx(odataNode);

        // The typed form's Capabilities section — the generic key/value grid this
        // replaces had no switches at all, only key/value/type rows.
        expect(screen.getByRole("switch", { name: "Add row" })).toBeInTheDocument();
        // …and the data-source note, which is the whole point of the typed form:
        // an OData table resolves its source at runtime, so there is no endpoint to ask for.
        expect(screen.getByText(/__tblProcess_file_id/)).toBeInTheDocument();
    });

    it("still shows the empty state when nothing is selected", () => {
        renderWithCtx(null);

        expect(screen.getByText("Select a node to configure")).toBeInTheDocument();
    });
});
